"""sim_hardware.py: a simulated instrument behind the AIMS MCP server.

Drop-in replacement for ``hardware_connections.HardwareManager`` so that the unmodified
``mcp_server_sse.py`` serves every navigation and measurement tool without a microscope:

* Navigation: a virtual Attocube stage over the real optical template
  (``templates/ArrowMarker_10x.jpg``). Each ``scan`` renders the 53 um MIM-like image the
  tip would see at its true position (crop of the template, actuator noise, row drift,
  a tip-retraction defect with probability ``p_defect``) and writes it as a genuine Nanonis
  ``.sxm`` file that pySPM and the server's own tools read. The particle filter, GP
  reconstruction and movement tools then run exactly as on the instrument.
* Measurement: gate ramps and scans at small frames replay the recorded gate-sweep images
  in ``examples/VbgScans`` (nearest recorded V_bg), and the generic sweeper replays the
  recorded grid spectra in ``examples/GridSpectra`` in order, so the twist-angle, SSIM and
  GWC analysis tools see real device data.
* Ground truth (true tip position, scan defects, replayed file for each call) is logged to
  ``<session>/truth.jsonl`` for the console and for evaluation; the agent never sees it.

What is not modelled: tip approach physics (approach_and_lift always succeeds), scan
timing (a configurable pause stands in for acquisition time), temperature, and any
dependence of the replayed spectra on the virtual position within the measurement frame.

Use through ``start_server_sim.py``; nothing in the team's code is edited.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("sim_hardware")

# Template frame (confirmed with Siyuan for the published run): origin pixel of the paper's
# coordinate frame on ArrowMarker_10x.jpg, and the scale of that image.
ORIGIN_PX = (867.0, 498.0)
UM_PER_PX = 0.875496
SCAN_UM_DEFAULT = 53.0
NAV_FRAME_MIN_UM = 10.0          # frames wider than this are navigation scans (synthetic);
                                 # narrower frames are measurement scans (replayed)
# True displacement of the simulated stage per step, um. start_server_sim.py sets it at start-up to
# the calibration the team's controller itself computes with (centering_controller, behind
# calculate_movement_from_prediction), so that a move lands where the controller intends. The
# values here are that controller's built-in ones and serve only when this module is used alone.
# Until 2026-11-12 this was -0.3 and 0.7, the values of the repository's config/stage_config.yaml,
# a file the server does not find when it runs in mcpServer/: every y move was 1.84 times too long.
STEP_UM = {"x": -0.28, "y": 0.38}


# --------------------------------------------------------------------------------------
# Nanonis .sxm writer (the subset of the format pySPM and the server's utils read)
# --------------------------------------------------------------------------------------
CHANNELS = [  # (id, name, unit, calibration)  mirrors the recorded files' DATA_INFO
    (30, "Z", "m", "-1.500E-6"), (1, "MIM-Re", "V", "1.000E+0"), (2, "MIM-Im", "V", "1.000E+0"),
    (3, "MIM-Re_AC-X", "V", "5.000E-2"), (4, "MIM-Re_AC-Y", "V", "5.000E-2"),
    (5, "MIM-Im_AC-X", "V", "1.000E-1"), (6, "MIM-Im_AC-Y", "V", "1.000E-1"),
    (7, "Encoder_Sense", "V", "1.000E+0"), (24, "Output_1", "V", "1.000E+0"),
    (25, "HEMT_Amp_Vd", "V", "1.000E+0"), (26, "HEMT_Amp_Vg", "V", "1.000E+0"),
    (27, "TopGate", "V", "1.000E+0"), (28, "X", "m", "3.000E-6"), (29, "Y", "m", "3.000E-6"),
    (0, "Temperature", "K", "3.600E-1"), (8, "BottomGate", "V", "1.000E+0"),
]


def write_sxm(path: Path, images: Dict[str, np.ndarray], pixels: int, lines: int,
              range_m: Tuple[float, float], offset_m: Tuple[float, float], angle_deg: float,
              direction: str, comment: str = "", extra_header: Optional[Dict[str, str]] = None,
              rec_temp_k: float = 6.5):
    """Write a Nanonis SXM file. ``images`` maps channel name -> (lines, pixels) float array;
    channels not given are written as zeros. Forward and backward frames are both written
    (backward = forward with a little extra noise), big-endian float32, DATA_INFO order."""
    now = datetime.now()
    h = []
    h.append(":NANONIS_VERSION:\n2\n")
    h.append(":SCANIT_TYPE:\n              FLOAT            MSBFIRST\n")
    h.append(f":REC_DATE:\n {now.strftime('%d.%m.%Y')}\n")
    h.append(f":REC_TIME:\n{now.strftime('%H:%M:%S')}\n")
    h.append(f":REC_TEMP:\n      {rec_temp_k:.10f}\n")
    h.append(":ACQ_TIME:\n       1.0\n")
    h.append(f":SCAN_PIXELS:\n       {pixels}       {lines}\n")
    h.append(f":SCAN_FILE:\n{path}\n")
    h.append(":SCAN_TIME:\n             1.000E+0             1.000E+0\n")
    h.append(f":SCAN_RANGE:\n           {range_m[0]:.6E}           {range_m[1]:.6E}\n")
    h.append(f":SCAN_OFFSET:\n             {offset_m[0]:.6E}         {offset_m[1]:.6E}\n")
    h.append(f":SCAN_ANGLE:\n            {angle_deg:.3E}\n")
    h.append(f":SCAN_DIR:\n{direction}\n")
    h.append(":BIAS:\n            0.000E+0\n")
    h.append(":COMMENT:\n" + (comment + "\n" if comment else "\n"))
    for k, v in (extra_header or {}).items():
        h.append(f":{k}:\n{v}\n")
    h.append(":DATA_INFO:\n\tChannel\tName\tUnit\tDirection\tCalibration\tOffset\n")
    for cid, name, unit, cal in CHANNELS:
        h.append(f"\t{cid}\t{name}\t{unit}\tboth\t{cal}\t0.000E+0\n")
    h.append("\n:SCANIT_END:\n\n")
    header = "".join(h).encode("latin-1")
    rng = np.random.default_rng(int(now.timestamp() * 1e3) % (2 ** 32))
    with open(path, "wb") as f:
        f.write(header)
        f.write(b"\x1a\x04")
        for _, name, _, _ in CHANNELS:
            img = images.get(name)
            if img is None:
                img = np.zeros((lines, pixels), dtype=np.float32)
            img = np.asarray(img, dtype=np.float32).reshape(lines, pixels)
            back = img + rng.normal(0, 0.01 * (np.std(img) + 1e-9), img.shape).astype(np.float32)
            f.write(struct.pack(f">{img.size}f", *img.ravel().tolist()))
            f.write(struct.pack(f">{back.size}f", *back.ravel().tolist()))


# --------------------------------------------------------------------------------------
# Navigation world: virtual stage over the real template
# --------------------------------------------------------------------------------------
class NavWorld:
    """True tip position in the paper frame (um, origin at ORIGIN_PX, y up) over the real
    template; renders the MIM-like image a 53 um scan would see there."""

    def __init__(self, template_path: str, seed: int = 0, init_r_um: float = 500.0,
                 p_defect: float = 0.30, noise: float = 0.07, row_drift: float = 0.05,
                 move_scale_err: float = 0.02, move_add_err_um: float = 2.5,
                 origin_px: Tuple[float, float] = ORIGIN_PX, um_per_px: float = UM_PER_PX,
                 start_xy_um: Optional[Tuple[float, float]] = None):
        import cv2
        tpl = cv2.imread(str(template_path))
        if tpl is None:
            raise FileNotFoundError(template_path)
        self.t = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        self.rng = np.random.default_rng(seed)
        self.origin_px, self.um_per_px = origin_px, um_per_px
        self.p_defect, self.noise, self.row_drift = p_defect, noise, row_drift
        self.move_scale_err, self.move_add_err = move_scale_err, move_add_err_um
        H, W = self.t.shape
        self.lim_x = (-(origin_px[0] - 60) * um_per_px, (W - origin_px[0] - 60) * um_per_px)
        self.lim_y = (-(H - origin_px[1] - 60) * um_per_px, (origin_px[1] - 60) * um_per_px)
        if start_xy_um is None:
            # A start at init_r_um in a random direction can lie beyond the template's edge (the template is
            # 953 um high), where a scan shows no marker at all. Draw the direction again until the start
            # is inside the stage limits. A seed whose first draw is inside keeps its start and its stream.
            for _ in range(200):
                ang = self.rng.uniform(0, 2 * np.pi)
                self.pos = init_r_um * np.array([np.cos(ang), np.sin(ang)])
                if self.lim_x[0] <= self.pos[0] <= self.lim_x[1] and self.lim_y[0] <= self.pos[1] <= self.lim_y[1]:
                    break
            else:                                             # init_r_um larger than the template: clip
                self.pos = np.array([np.clip(self.pos[0], *self.lim_x), np.clip(self.pos[1], *self.lim_y)])
        else:
            self.pos = np.array(start_xy_um, dtype=float)
        self.n_scans = self.n_moves = 0
        self.last_defect = False

    def true_xy(self) -> Tuple[float, float]:
        return float(self.pos[0]), float(self.pos[1])

    def true_r(self) -> float:
        return float(np.hypot(*self.pos))

    def scan(self, frame_um: float, nx: int, ny: int, lift: bool = False, p_override: Optional[float] = None) -> np.ndarray:
        import cv2
        self.n_scans += 1
        px = self.origin_px[0] + self.pos[0] / self.um_per_px
        py = self.origin_px[1] - self.pos[1] / self.um_per_px
        h = frame_um / self.um_per_px / 2
        H, W = self.t.shape
        y0, y1 = int(max(py - h, 0)), int(min(py + h, H))
        x0, x1 = int(max(px - h, 0)), int(min(px + h, W))
        crop = self.t[y0:y1, x0:x1] if (y1 > y0 and x1 > x0) else np.zeros((2, 2), np.float32)
        img = cv2.resize(crop, (nx, ny), interpolation=cv2.INTER_AREA).astype(np.float32)
        p = 0.02 if lift else self.p_defect
        if p_override is not None:
            p = float(p_override)
        self.last_p = p
        self.last_defect = bool(self.rng.random() < p)
        if self.last_defect:                                  # tip retraction: rows die
            r0 = int(self.rng.integers(2, max(3, ny - 4)))
            img[r0:] = img[r0:] * 0.05 + self.rng.normal(0, 0.14, img[r0:].shape)
        img += np.cumsum(self.rng.normal(0, self.row_drift, ny))[:, None]
        img += self.rng.normal(0, self.noise, img.shape)
        return img

    def move(self, dx_um: float, dy_um: float):
        self.n_moves += 1
        e = np.array([dx_um, dy_um]) * (1 + self.rng.normal(0, self.move_scale_err, 2)) \
            + self.rng.normal(0, self.move_add_err, 2)
        new = self.pos + e
        self.pos = np.array([np.clip(new[0], *self.lim_x), np.clip(new[1], *self.lim_y)])


# --------------------------------------------------------------------------------------
# Measurement replay: recorded gate-sweep images and grid spectra
# --------------------------------------------------------------------------------------
class Replay:
    def __init__(self, examples_dir: str):
        ex = Path(examples_dir)
        self.vbg_files = sorted((ex / "VbgScans").glob("*.sxm"))
        self.grid_files = sorted((ex / "GridSpectra").glob("*.dat"))
        self.vbg_values = self._vbg_axis()
        self.grid_i = 0
        if not self.vbg_files:
            logger.warning("no VbgScans found under %s; measurement scans will be synthetic noise", ex)
        if not self.grid_files:
            logger.warning("no GridSpectra found under %s; sweeper will write empty files", ex)

    def _vbg_axis(self) -> List[float]:
        """V_bg of each recorded file: from a 'Vbg=a-bV' tag in the first header if present,
        else an even ladder that the caller labels by index."""
        n = len(self.vbg_files)
        if n == 0:
            return []
        try:
            raw = self.vbg_files[0].read_bytes()
            hdr = raw[:raw.find(b"\x1a\x04")].decode("latin-1", errors="ignore")
            m = re.search(r"Vbg=([-\d.]+)-([-\d.]+)V", hdr)
            if m and n > 1:
                a, b = float(m.group(1)), float(m.group(2))
                return list(np.linspace(a, b, n))
        except Exception:
            pass
        return list(np.linspace(0.0, 1.0, n))

    def nearest_vbg_file(self, vbg: float) -> Tuple[Optional[Path], Optional[float]]:
        if not self.vbg_files:
            return None, None
        i = int(np.argmin(np.abs(np.array(self.vbg_values) - vbg)))
        return self.vbg_files[i], self.vbg_values[i]

    def next_grid_file(self) -> Optional[Path]:
        if not self.grid_files:
            return None
        p = self.grid_files[self.grid_i % len(self.grid_files)]
        self.grid_i += 1
        return p


# --------------------------------------------------------------------------------------
# The simulated Nanonis controller (the methods the server's tools call)
# --------------------------------------------------------------------------------------
class SimNanonis:
    def __init__(self, world: NavWorld, replay: Replay, session_dir: Path,
                 scan_seconds: float = 0.5, posix_alias: bool = True):
        self.world, self.replay = world, replay
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.scan_seconds = scan_seconds
        self.posix_alias = posix_alias and os.name != "nt"
        self.vbg = 0.0
        self.vtg = 0.0
        self.xy = (0.0, 0.0)                        # Nanonis fine-scanner position, m
        self.frame = (0.0, 0.0, SCAN_UM_DEFAULT * 1e-6, SCAN_UM_DEFAULT * 1e-6, 0.0)
        self.series = "scan"
        self.pixels, self.lines = 128, 40
        self.speed = 1.9e-6
        self.z_on = 1
        # AIMS_SIM_COUPLE_CONTROLLER=1 (off by default, added 2026-11-11 for the navigation rule test):
        # a navigation scan that suffers the tip-retraction defect leaves the Z controller OFF, as on
        # the instrument, so the scan tool reports "failed" and check_controller_status reads OFF; and
        # the defect probability falls with tip lift (p_defect at the prompt's 500 nm, halved for every
        # further 100 nm, floor 0.02), so that "repeat with 100 nm more tip lift" has an effect.
        # Without the flag a defect only degrades the image and the controller always reads ON.
        self.couple_controller = os.environ.get("AIMS_SIM_COUPLE_CONTROLLER") == "1"
        self.tip_lift = 5e-7
        self.ids: Dict[str, int] = {}
        self.sweep_n = 0
        self.last_file: Optional[Path] = None
        self.truth = self.session_dir / "truth.jsonl"
        self._log({"event": "session_start", "true_xy_um": self.world.true_xy()})

    # -- bookkeeping -------------------------------------------------------------------
    def _log(self, rec: dict):
        rec = {"t": datetime.now().isoformat(timespec="seconds"), **rec}
        with open(self.truth, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _next_path(self, series: str, ext: str = ".sxm") -> Tuple[Path, str]:
        n = self.ids.get(series, 0) + 1
        self.ids[series] = n
        sid = f"{n:04d}"
        return self.session_dir / f"{series}{sid}{ext}", sid

    def _alias(self, path: Path):
        """The server builds paths as session_path + '\\' + name (Windows). On POSIX that is
        a file called '<dir>\\<name>' in the parent directory; keep one so the unmodified
        tools find the scan."""
        if self.posix_alias:
            alias = self.session_dir.parent / f"{self.session_dir.name}\\{path.name}"
            try:
                if alias.exists():
                    alias.unlink()
                os.link(path, alias)
            except OSError:
                shutil.copy2(path, alias)

    # -- signals and outputs -----------------------------------------------------------
    def sig_val_get_by_index(self, idx: int):
        return {31: self.vbg, 27: self.vtg}.get(int(idx), 0.0)

    def AO_volt_set_by_index(self, ao: int, V: float):
        if int(ao) == 8:
            self.vbg = float(V)
        elif int(ao) == 4:
            self.vtg = float(V)

    # -- scan setup --------------------------------------------------------------------
    def SpeedSet(self, fwd_speed=1.9e-6, bwd_speed=1.9e-6):
        self.speed = float(fwd_speed)

    def BufferSet(self, pixels=128, lines=40):
        self.pixels, self.lines = int(pixels), int(lines)

    def PropsSet(self, series_name="scan", **kw):
        self.series = str(series_name)

    def PropsGet(self):
        # the real call returns a tuple whose 4th element is the series name
        return (0, 0, "", self.series)

    def FrameSet(self, x, y, width, height, angle=0.0):
        self.frame = (float(x), float(y), float(width), float(height), float(angle))

    def FrameGet(self):
        return self.frame

    def XY_pos_set(self, x, y, wait_end=1):
        self.xy = (float(x), float(y))
        time.sleep(0.02)

    def tip_stop(self):
        pass

    def z_controller_OnOffGet(self):
        return self.z_on

    def z_withdraw(self):
        self.z_on = 0

    def session_path_get(self) -> str:
        return str(self.session_dir)

    # -- acquisition -------------------------------------------------------------------
    def Action(self, action: str, direction: str = "up"):
        self.direction = direction
        self.z_on = 1

    def WaitEndOfScan(self, timeout=-1):
        time.sleep(max(0.0, self.scan_seconds))
        path, sid = self._next_path(self.series)
        width_um = self.frame[2] * 1e6
        if width_um > NAV_FRAME_MIN_UM:                 # navigation: synthetic from template
            p_used = None
            if self.couple_controller:
                extra = max(0.0, (self.tip_lift - 5e-7) / 1e-7)
                p_used = max(0.02, self.world.p_defect * 0.5 ** extra)
            img = self.world.scan(width_um, self.pixels, self.lines, p_override=p_used)
            if self.couple_controller and self.world.last_defect:
                self.z_on = 0                           # the tip retracted during the scan
            # the server flips the stored image vertically (np.flipud) when it reads it
            stored = np.flipud(img)
            write_sxm(path, {"MIM-Im_AC-X": stored, "MIM-Re_AC-X": 0.5 * stored,
                             "MIM-Im": 0.2 * stored, "BottomGate": np.full(stored.shape, self.vbg, np.float32)},
                      self.pixels, self.lines, (self.frame[2], self.frame[3]),
                      (self.frame[0], self.frame[1]), self.frame[4], getattr(self, "direction", "up"),
                      comment="simulated navigation scan")
            self._log({"event": "scan", "file": str(path), "kind": "navigation", "frame_um": width_um,
                       "true_xy_um": self.world.true_xy(), "true_r_um": self.world.true_r(),
                       "defect": self.world.last_defect, "controller_on": bool(self.z_on),
                       "tip_lift_m": self.tip_lift, "p_defect_used": getattr(self.world, "last_p", None),
                       "coupled": self.couple_controller})
        else:                                           # measurement: replay a recorded image
            src, v = self.replay.nearest_vbg_file(self.vbg)
            if src is not None:
                shutil.copy2(src, path)
            else:
                write_sxm(path, {}, self.pixels, self.lines, (self.frame[2], self.frame[3]),
                          (self.frame[0], self.frame[1]), self.frame[4], getattr(self, "direction", "up"))
            self._log({"event": "scan", "file": str(path), "kind": "measurement_replay",
                       "vbg_requested": self.vbg, "vbg_replayed": v, "source": str(src) if src else None})
        self._alias(path)
        self.last_file = path
        return False, path.stat().st_size, str(path)

    def GS_start(self, basename: str, get_data=1, sweep_direction=1, reset_signal=0):
        time.sleep(max(0.0, self.scan_seconds * 0.2))
        src = self.replay.next_grid_file()
        path, _ = self._next_path(str(basename), ".dat")
        if src is not None:
            shutil.copy2(src, path)
        else:
            path.write_text("")
        self._log({"event": "sweep", "file": str(path), "source": str(src) if src else None,
                   "xy_m": self.xy, "vbg": self.vbg, "direction": int(sweep_direction)})
        return str(path)

    def close(self):
        self._log({"event": "session_end", "true_xy_um": self.world.true_xy(),
                   "n_scans": self.world.n_scans, "n_moves": self.world.n_moves})


# --------------------------------------------------------------------------------------
# The HardwareManager stand-in
# --------------------------------------------------------------------------------------
class SimHardwareManager:
    """Same interface as hardware_connections.HardwareManager. Configured through
    environment variables so the unmodified server can construct it with no arguments:
      AIMS_SIM_TEMPLATE   path to ArrowMarker_10x.jpg
      AIMS_SIM_EXAMPLES   path to the examples directory (VbgScans, GridSpectra)
      AIMS_SIM_SESSIONS   directory for simulated sessions (default ./sessions/sim)
      AIMS_SIM_SEED, AIMS_SIM_P_DEFECT, AIMS_SIM_INIT_R_UM, AIMS_SIM_START_XY (\"x,y\" um),
      AIMS_SIM_SCAN_SECONDS
    One world is shared by every manager instance in the process (the server constructs
    several), so the stage keeps its position across tools."""

    _shared: Dict[str, object] = {}

    def __init__(self):
        self.nanonis: Optional[SimNanonis] = None
        self.attocube = None
        self.available_axes = None
        self.hardware_ready = False

    @classmethod
    def _world(cls) -> Tuple[NavWorld, Replay, SimNanonis]:
        if not cls._shared:
            tpl = os.environ.get("AIMS_SIM_TEMPLATE", "../templates/ArrowMarker_10x.jpg")
            ex = os.environ.get("AIMS_SIM_EXAMPLES", "../examples")
            sessions = Path(os.environ.get("AIMS_SIM_SESSIONS", "sessions/sim"))
            seed = int(os.environ.get("AIMS_SIM_SEED", "0"))
            start = os.environ.get("AIMS_SIM_START_XY")
            start_xy = tuple(float(v) for v in start.split(",")) if start else None
            world = NavWorld(tpl, seed=seed, p_defect=float(os.environ.get("AIMS_SIM_P_DEFECT", "0.30")),
                             init_r_um=float(os.environ.get("AIMS_SIM_INIT_R_UM", "500")), start_xy_um=start_xy)
            replay = Replay(ex)
            sess = sessions / datetime.now().strftime("%Y%m%d_%H%M%S")
            nano = SimNanonis(world, replay, sess, scan_seconds=float(os.environ.get("AIMS_SIM_SCAN_SECONDS", "0.5")))
            cls._shared.update(world=world, replay=replay, nanonis=nano)
            logger.info("simulated instrument ready: session %s, true start %s um", sess, world.true_xy())
        return cls._shared["world"], cls._shared["replay"], cls._shared["nanonis"]

    def connect_all(self, nanonis_address="TCPIP0::localhost::6501::SOCKET", attocube_address="COM6"):
        world, replay, nano = self._world()
        self.nanonis = nano
        self.attocube = "simulated"          # truthy: the tools test `hw.attocube`
        self.available_axes = [4, 5]
        self.hardware_ready = True
        return True

    def move_tip(self, x_steps, y_steps, x_volt=60, y_volt=60, x_freq=100, y_freq=100):
        world, _, nano = self._world()
        dx, dy = float(x_steps) * STEP_UM["x"], float(y_steps) * STEP_UM["y"]
        world.move(dx, dy)
        time.sleep(0.05)
        nano._log({"event": "move", "x_steps": float(x_steps), "y_steps": float(y_steps),
                   "commanded_um": [dx, dy], "um_per_step": [STEP_UM["x"], STEP_UM["y"]],
                   "true_xy_um": world.true_xy(), "true_r_um": world.true_r()})
        return {"x_steps": float(x_steps), "y_steps": float(y_steps),
                "nominal_displacement_um": [round(dx, 2), round(dy, 2)], "status": "moved"}

    def approach_and_lift(self, tip_lift=500e-9, high_limit=3e-6, low_limit=-3e-6):
        _, _, nano = self._world()
        time.sleep(0.05)
        nano.z_on = 1
        nano.tip_lift = float(tip_lift)
        nano._log({"event": "approach", "tip_lift_m": float(tip_lift)})
        return {"status": "approached and lifted", "tip_lift_m": float(tip_lift)}

    def scan_and_retract(self, series_name, direction="up", scan_speed=1.9e-6, pixels=128, lines=40):
        _, _, nano = self._world()
        nano.SpeedSet(scan_speed, scan_speed)
        nano.BufferSet(pixels, lines)
        nano.PropsSet(series_name=series_name)
        nano.Action("start", direction)
        _, _, path = nano.WaitEndOfScan()
        nano.z_withdraw()
        return path

    def get_sxm_image(self, id="0001", channel="MIM-Im_AC-X", direction="forward"):
        from pySPM import SXM
        from stripe_removal import remove_stripes_row_poly
        _, _, nano = self._world()
        path = nano.session_dir / f"{nano.PropsGet()[3]}{id}.sxm"
        img = SXM(str(path)).get_channel(channel, direction=direction).pixels
        return remove_stripes_row_poly(img, deg=1)

    def disconnect_all(self):
        self.nanonis = None
        self.attocube = None
        self.hardware_ready = False

    def __enter__(self):
        self.connect_all()
        return self

    def __exit__(self, *exc):
        self.disconnect_all()
