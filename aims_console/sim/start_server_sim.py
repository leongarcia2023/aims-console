#!/usr/bin/env python3
"""start_server_sim.py: run the team's MCP server (mcp_server_sse.py) on a simulated instrument.

    python start_server_sim.py --mcp-dir /path/to/AIMS/mcpServer [--port 8000]
        [--template /path/to/templates/ArrowMarker_10x.jpg] [--examples /path/to/examples]
        [--seed 0] [--p-defect 0.30] [--init-r 500 | --start-xy X,Y] [--scan-seconds 0.5]
        [--stage-step-um=X,Y]

Nothing in the team's code is modified. The server does
``from hardware_connections import HardwareManager`` inside each tool and
``centering_controller`` imports the same name at module level; both are pointed at
``sim_hardware.SimHardwareManager`` before ``mcp_server_sse`` is imported, so every tool
runs against the virtual stage, the synthetic navigation scans and the replayed
measurement data. The SSE endpoint is the same (http://localhost:PORT/sse), so LibreChat,
the console, or any MCP client connects exactly as it would to the real instrument.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def controller_step_um(server, cc):
    """um per step in x and y as the team's controller turns a movement into steps
    (CenteringController._calculate_movement: the server's stage_config if it found one, else the
    controller's built-in values). Measured by asking it for a 90 um move along each axis."""
    import types
    import numpy as np
    cfg = getattr(server, "stage_config", None) or {}
    att = cfg.get("attocube") or {}
    probe = types.SimpleNamespace(stage_config=cfg)
    out = []
    for axis, pos in (("x", (-90.0, 0.0)), ("y", (0.0, -90.0))):
        mv = cc.CenteringController._calculate_movement(probe, np.array(pos), 1.0, 90.0)
        measured = 90.0 / mv[f"{axis}_steps"]                    # whole steps: good to about 0.2 percent
        stated = att.get(f"microns_per_step_{axis}")
        if stated is not None and abs(float(stated) / measured - 1) < 0.01:
            out.append(float(stated))
        elif abs(round(measured, 2) / measured - 1) < 0.005:
            out.append(round(measured, 2))
        else:
            out.append(round(measured, 4))
    return tuple(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp-dir", required=True, help="the repository's mcpServer directory")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--template", default=None, help="default: <mcp-dir>/../templates/ArrowMarker_10x.jpg")
    ap.add_argument("--examples", default=None, help="default: <mcp-dir>/../examples")
    ap.add_argument("--sessions", default=None, help="default: <mcp-dir>/sessions/sim")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--p-defect", type=float, default=0.30)
    ap.add_argument("--init-r", type=float, default=500.0, help="initial tip distance from the target, um")
    ap.add_argument("--start-xy", default=None, help="initial true position 'x,y' in um (overrides --init-r)")
    ap.add_argument("--scan-seconds", type=float, default=0.5, help="pause per simulated scan")
    ap.add_argument("--bind", default="0.0.0.0", help="interface to listen on (127.0.0.1 for per-run servers behind the console)")
    ap.add_argument("--couple-controller", action="store_true",
                    help="a scan with the tip-retraction defect leaves the Z controller OFF, and more tip lift lowers "
                         "the defect probability (off by default; used by the navigation rule test)")
    ap.add_argument("--threaded-tools", action="store_true",
                    help="the default since 2026-11-12, kept so that older command lines still work: each tool runs in a "
                         "worker thread (one at a time) so the SSE keep-alive keeps flowing during a long call "
                         "(threaded_tools.py)")
    ap.add_argument("--blocking-tools", action="store_true",
                    help="run the tools on the server's event loop, as the team's server does. A tool that computes for "
                         "more than about five minutes (GP regression on a small machine) then loses its answer")
    ap.add_argument("--stage-step-um", default=None,
                    help="true displacement of the simulated stage per step, 'x,y' in um. Default: the calibration the "
                         "team's controller computes with, so a move lands where it intends. Write it with an equals "
                         "sign: --stage-step-um=-0.3,0.7 gives the stage of the runs before 2026-11-12, whose y moves "
                         "were 1.84 times too long")
    ap.add_argument("--heavy-lock", default=None,
                    help="a file shared by the simulators of one console: GP regression (a peak of about 5.4 GB) "
                         "takes an exclusive lock on it, so only one runs at a time across the console's runs")
    a = ap.parse_args()

    mcp_dir = Path(a.mcp_dir).resolve()
    if not (mcp_dir / "mcp_server_sse.py").exists():
        sys.exit(f"{mcp_dir} does not contain mcp_server_sse.py")
    os.environ["AIMS_SIM_TEMPLATE"] = str(Path(a.template or mcp_dir.parent / "templates" / "ArrowMarker_10x.jpg").resolve())
    os.environ["AIMS_SIM_EXAMPLES"] = str(Path(a.examples or mcp_dir.parent / "examples").resolve())
    os.environ["AIMS_SIM_SESSIONS"] = str(Path(a.sessions or mcp_dir / "sessions" / "sim").resolve())
    os.environ["AIMS_SIM_SEED"] = str(a.seed)
    os.environ["AIMS_SIM_P_DEFECT"] = str(a.p_defect)
    os.environ["AIMS_SIM_INIT_R_UM"] = str(a.init_r)
    if a.start_xy:
        os.environ["AIMS_SIM_START_XY"] = a.start_xy
    os.environ["AIMS_SIM_SCAN_SECONDS"] = str(a.scan_seconds)
    if a.couple_controller:
        os.environ["AIMS_SIM_COUPLE_CONTROLLER"] = "1"
    os.environ["AIMS_SIM"] = "1"

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(mcp_dir))
    os.chdir(mcp_dir)                       # the server resolves config/ and sessions/ relative to its directory
    # the loop prompts name templates\... and examples\... ; make both reachable from here
    for name, target in (("templates", Path(os.environ["AIMS_SIM_TEMPLATE"]).parent),
                         ("examples", Path(os.environ["AIMS_SIM_EXAMPLES"]))):
        link = mcp_dir / name
        if not link.exists():
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    import sim_hardware
    import hardware_connections
    hardware_connections.HardwareManager = sim_hardware.SimHardwareManager
    import centering_controller
    centering_controller.HardwareManager = sim_hardware.SimHardwareManager
    import mcp_server_sse
    mcp_server_sse.HardwareManager = sim_hardware.SimHardwareManager  # in case a tool binds the name at import

    ctrl = controller_step_um(mcp_server_sse, centering_controller)
    if a.stage_step_um:
        sx, sy = (float(v) for v in a.stage_step_um.split(","))
    else:
        sx, sy = ctrl
    sim_hardware.STEP_UM.update(x=sx, y=sy)

    import uvicorn
    from mcp_server_sse import mcp, create_starlette_app
    n_tools = len(getattr(mcp, "_tool_manager")._tools)
    if not a.blocking_tools:
        from threaded_tools import make_tools_threaded
        lock = str(Path(a.heavy_lock).resolve()) if a.heavy_lock else None
        print(f"  tools run in worker threads, one at a time: {make_tools_threaded(mcp, heavy_lock=lock)} wrapped"
              + (f"; GP regression waits on {lock}" if lock else ""))
    print(f"AIMS MCP server on a SIMULATED instrument: {n_tools} tools, SSE at http://localhost:{a.port}/sse")
    print(f"  stage: {sx} and {sy} um per step in x and y; the controller computes with {ctrl[0]} and {ctrl[1]}"
          + ("" if (sx, sy) == ctrl else "  (NOT the same: moves will not land where the controller intends)"))
    print(f"  template {os.environ['AIMS_SIM_TEMPLATE']}\n  examples {os.environ['AIMS_SIM_EXAMPLES']}\n"
          f"  sessions {os.environ['AIMS_SIM_SESSIONS']}\n  seed {a.seed}, p_defect {a.p_defect}, "
          f"start {'xy=' + a.start_xy if a.start_xy else f'r={a.init_r} um'}")
    app = create_starlette_app(mcp._mcp_server, debug=False)
    config = uvicorn.Config(app, host=a.bind, port=a.port, log_level="info")
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
