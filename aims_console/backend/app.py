"""app.py: the AIMS console service.

    python -m backend.app --repo /path/to/AIMS --mcp-url http://localhost:8000/sse [--sim] [--port 8080]
        [--model claude-opus-4-5] [--packet /path/to/discoveryLoopPacket] [--runs runs_console]
    python -m backend.app --repo /path/to/AIMS --sim-per-run --public --origins https://leonmgarcia.com [--port 8080]

Serves the console page and a small API:
  GET  /api/config                 mode, MCP URL, models, loops, prompts, limits
  GET  /api/tools                  the MCP server's tool list (name, description)
  GET  /api/runs?client=           runs (newest first); in public mode only the caller's
  POST /api/runs                   start a run {loop, prompt, model, api_key?, max_turns, approve, handoff, client, use_skills}
  GET  /api/runs/{id}              meta + result
  GET  /api/runs/{id}/events.jsonl the run's log
  POST /api/runs/{id}/approve      {approval_id, decision: allow|deny, note}
  POST /api/runs/{id}/message      {text}   operator message as the next turn
  POST /api/runs/{id}/stop
  WS   /ws/runs/{id}               event stream (history first, then live)

Two deployments. On the lab machine the console talks to one MCP server (real or simulated) and
uses the signed-in CLI; runs are shared by everyone at the console. In public mode (--public,
--sim-per-run) each run gets its own simulated instrument, must bring its own API key (Anthropic
or OpenAI), is capped in turns and wall time, is visible only to the browser that started it,
and the discovery loop (file and shell tools) is not offered. Keys live in the run's process
environment or request headers for the duration of the run and are never written to disk.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .openai_runner import OpenAIRun
from .runner import BaseRun, ClaudeRun, install_skills
from .simpool import SimServer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB = ROOT / "web"
SKILLS = ROOT / "skills"
CFG: Dict[str, object] = {}
RUNS: Dict[str, object] = {}
TOOLS_CACHE: Optional[dict] = None

LOOP_FILES = {"navigation": "Navigation loop prompt.md", "measurement": "Measurement loop prompt.md",
              "discovery": "Discovery loop prompt.md"}

# the models of the study, plus current ones for cheap tries; the page offers these and a free field
MODELS = [
    {"id": "claude-opus-4-5", "provider": "anthropic", "label": "Claude Opus 4.5", "study": "navigation and measurement loops in the paper; second-model check of the discovery reruns"},
    {"id": "claude-opus-4-8", "provider": "anthropic", "label": "Claude Opus 4.8", "study": "discovery loop in the paper; the contamination reruns"},
    {"id": "gpt-5.5", "provider": "openai", "label": "GPT-5.5", "study": "benchmark in the paper"},
    {"id": "claude-opus-5-5", "provider": "anthropic", "label": "Claude Opus 5.5", "study": "second-model check of the discovery reruns"},
    {"id": "claude-sonnet-5-5", "provider": "anthropic", "label": "Claude Sonnet 5.5", "study": "not in the study; cheaper"},
    {"id": "claude-haiku-5-5", "provider": "anthropic", "label": "Claude Haiku 5.5", "study": "not in the study; cheapest, used for the console's own tests"},
]

OPERATING_RULES = (
    "You are operating a microwave impedance microscope through the tools of the 'aims' MCP server. "
    "Call one tool at a time and wait for its result before the next call. Report each step's outcome in one or "
    "two sentences. If a tool returns an error, say what failed and choose the next step from the protocol; do not "
    "invent measurements. When the protocol's end condition is met, stop and "
    "write a short summary of the final state (position estimate and uncertainty, files produced, open issues)."
    # Until 2026-11-12 this also said "or a tool reports experiment_complete". predict_position reports that flag
    # whenever the predicted distance is under 50 um, whatever the uncertainty, so the sentence could end a run on a
    # prediction that step 7 of the navigation prompt sends to GP regression first (it did, at all five agent stops of
    # the second round of the navigation rule test). The stop is now the protocol's alone, as in the team's LibreChat
    # set-up, whose prompts and configuration do not mention the flag.
)

TEMPLATE_DEFAULTS = {"origin_x_px": 867, "origin_y_px": 498, "height_um": 952.54}   # the prompt's load_template call; particle_filter.Config.Z_HEIGHT

# Two ways to run the navigation loop (2026-11-14):
#  - protocol: the team's prompt, with its rules written in as numbered steps (step 3: controller off, approach
#    again with more lift; step 7: uncertainty above 53 um, GP regression), as in the paper's runs; no skills.
#  - objective: OBJECTIVE_PROMPT, the goal and the fixed parameters only, no steps and no rules; the backend installs
#    the two skills (skills/aims-controller-recovery, skills/aims-gp-recovery), which describe how the two main
#    uncertainties of navigation show in the tool outputs and what the remedies are, and the agent decides whether
#    and when to read them. Neither the prompt nor the system prompt names a skill or a step; the agent sees the
#    skills in its tool list, as any installed skill.
# History: until 2026-11-13 the console rewrote steps 3 and 7 of the team's prompt into one-line instructions to use
# the skills; on 2026-11-13 it kept the team's prompt and added SKILLS_RULE to the system prompt. Runs recorded then
# carry those prompts in their meta.json.
SKILLS_RULE = (
    "Two skills are installed for the navigation protocol: aims-controller-recovery carries out step 3 and "
    "aims-gp-recovery carries out step 7. When the protocol reaches one of these steps, invoke the skill for that "
    "step with the Skill tool and let it carry the step out."
)   # not used since 2026-11-14; kept for the runs of 2026-11-13

OBJECTIVE_RULES = (
    "You are operating a microwave impedance microscope through the tools of the 'aims' MCP server. "
    "Call one tool at a time and wait for its result before the next call. Report each step's outcome in one or "
    "two sentences. If a tool returns an error, say what failed and decide what to do next; do not invent "
    "measurements. When the task's end condition is met, stop and write a short summary of the final state "
    "(position estimate and uncertainty, files produced, open issues)."
)

OBJECTIVE_PROMPT = """# Navigation task

Bring the tip to within {stop_um:g} um of the target, the origin of the navigation template. How you get there is up to you: choose the tools and their order yourself.

What you need to know:
- The navigation template is {template} (template_height_um 953, template_width_um 1433, origin_x_px 867, origin_y_px 498). Load it before the first position estimate.
- A locating scan is taken after an approach with -3e-6 m as the low limit, 3e-6 m as the high limit and a 500 nm tip lift. It uses the series name "sampleLocating", 128 pixels and 40 lines, and its frame is 53 x 53 um. The position prediction takes a 256 x 256 image.
- Stage moves use 50 V and 100 Hz for both x and y, with the tip retracted.
- The task is done when the predicted distance from the center is smaller than {stop_um:g}e-6 m.

DO NOT run any tools in parallel.
"""


def objective_prompt(repo: Path, stop_um: float) -> str:
    """The objective-only navigation prompt: the goal and the fixed parameters of the team's prompt, no steps, no rules
    (the goal-only arm G prepared for the navigation rule test on 2026-11-12, plus the retracted-tip condition of the
    team's step 4 and the template load of the console's step 0)."""
    return OBJECTIVE_PROMPT.format(stop_um=stop_um, template=repo / "templates" / "ArrowMarker_10x.jpg")


def is_objective(prompt: str) -> bool:
    """A navigation prompt in objective form (OBJECTIVE_PROMPT, possibly after a hand-off note)."""
    body = prompt.split("\n\n", 1)[1] if prompt.startswith("Hand-off note from the previous loop") and "\n\n" in prompt else prompt
    return body.lstrip().startswith("# Navigation task")


def system_prompt_for(loop: str, use_skills: bool, prompt: str = "") -> str:
    """The system prompt of an instrument run: the operating rules, in their objective form for an objective prompt.
    It names no skill: installed skills appear in the agent's tool list."""
    if loop == "navigation" and is_objective(prompt):
        return OBJECTIVE_RULES
    return OPERATING_RULES


# On the simulator the console adapts two of the team's prompts, in the open (a note under the prompt box says what
# changed), so that a run started with the defaults does what the recorded runs on the page did. The prompts released
# with the code are written for the microscope; CFG["prompts_released"] keeps them. (Added 2026-11-14.)
#  - navigation: steps 9 and 10 stop at 150 um, the termination criterion of the Fig. 2 run (Methods; the team
#    confirmed it on 2026-11-12 and said a simulator test on the marker chip should use 150 um). On the marker chip,
#    whose centre has no marker within 42 um, a run with the released 50 um most likely ends at a cap (MANUAL, 4).
#  - measurement: the blanks are filled to match the device data the simulator replays (recorded on 2026-03-14): gate
#    2.5 to 3.4 V in 10 steps, a 2 um x 0.5 um region, a 12 x 3 grid taken with two iterations (so four files per
#    point, back and forth), folder "measurement"; the hand-off note gives the device's scan frame, because a
#    navigation run on the simulator ends on the marker chip, not on the device.
SIM_STOP_UM = 150
SIM_FOLDER = "measurement"
SIM_HANDOFF = {
    "measurement": ("Navigation brought the tip onto the device. Before step 1, set the scan frame to the device region: "
                    "centre x = 13.278 um, y = 15.615 um, 3 um x 3 um, angle 39.8 degrees. The device is twisted MoSe2 with "
                    "a single bottom gate (no top gate); the bottom hBN is 15 nm thick."),
}
SIM_NOTES = {
    "navigation": ("Simulator: steps 9 and 10 stop at 150 µm, the termination criterion of the Fig. 2 run. The prompt released "
                   "with the code says 50 µm; on the marker chip, whose centre has no marker within 42 µm, a run with 50 µm "
                   "most likely ends at a cap (MANUAL, section 4). Edit the two numbers to run it as released."),
    "measurement": ("Simulator: the blanks are filled to match the device data the simulator replays (recorded on 14 March "
                    "2026): gate 2.5 to 3.4 V in 10 steps, a 2 µm × 0.5 µm region, a 12 × 3 grid taken with two "
                    "iterations, so four files per point as in the example prompt of SI Fig. S15, in the folder "
                    "\"measurement\". The hand-off note gives the "
                    "device's scan frame, since a navigation run on the simulator ends on the marker chip."),
}


OBJECTIVE_NOTE = ("Objective only: the prompt gives the goal and the fixed parameters, with no steps and no rules. The "
                  "backend gives the agent two skills, aims-controller-recovery and aims-gp-recovery, which describe how "
                  "an interrupted scan and an uncertain position estimate show in the tool outputs and what the remedies "
                  "are; the agent decides whether and when to read them. Untick the box for the team's prompt with the "
                  "rules written in, as in the paper's runs.")


def with_stop_radius(prompt: str, stop_um: float) -> str:
    """The navigation prompt with the stop radius of steps 9 and 10 (the released prompt's two "50e-6m") changed.
    Any other text (already changed, or edited) comes back unchanged."""
    new = f"{stop_um:g}e-6m"
    if new in prompt or prompt.count("50e-6m") != 2:
        return prompt
    return prompt.replace("50e-6m", new)


def fill_measurement(prompt: str, folder: str = SIM_FOLDER) -> str:
    """The measurement prompt with its blanks filled for the simulator's replayed device data. The grid step asks for
    two iterations and the averaging for four files, as the replayed grid was taken. Any other text comes back unchanged."""
    subs = [
        ("start with V1, stop at V2, with V_steps.", "start with 2.5 V, stop at 3.4 V, with 10 steps."),
        ('Use series name "folder\\scan"', f'Use series name "{folder}\\scan"'),
        ('on sxm data in "folder". Identify a a um x b um region. Show a grid with n x m points.',
         f'on sxm data in "{folder}". Identify a 2 um x 0.5 um region. Show a grid with 12 x 3 points.'),
        ("Set the scan frame to the a um x b um region", "Set the scan frame to the 2 um x 0.5 um region"),
        ('Use 10s wait and basename "folder\\grid".', f'Use 10s wait, 2 iterations and basename "{folder}\\grid".'),
        ('on grid data in "folder". Average every 2 files for back and forth.',
         f'on grid data in "{folder}". Average every 4 files (two iterations, back and forth).'),
        ('Use 4 iterations and basename "folder\\bestPoint".', f'Use 4 iterations and basename "{folder}\\bestPoint".'),
    ]
    out = prompt
    for old, new in subs:
        if out.count(old) != 1:
            return prompt
        out = out.replace(old, new)
    return out


def simulator_prompts(prompts: Dict[str, str]):
    """The prompts a simulator console shows, and a note per adapted loop (empty where a prompt is left as released)."""
    out, notes = dict(prompts), {}
    if "navigation" in out:
        nav = with_stop_radius(out["navigation"], SIM_STOP_UM)
        if nav != out["navigation"]:
            out["navigation"], notes["navigation"] = nav, SIM_NOTES["navigation"]
    if "measurement" in out:
        meas = fill_measurement(out["measurement"])
        if meas != out["measurement"]:
            out["measurement"], notes["measurement"] = meas, SIM_NOTES["measurement"]
    return out, notes


def provider_for(model: str) -> str:
    for m in MODELS:
        if m["id"] == model:
            return m["provider"]
    return "openai" if model.lower().startswith(("gpt", "o1", "o3", "o4")) else "anthropic"


def load_prompts(repo: Path) -> Dict[str, str]:
    out = {}
    for loop, fname in LOOP_FILES.items():
        p = repo / "prompts" / fname
        txt = p.read_text(encoding="utf-8") if p.exists() else f"# {loop} prompt not found at {p}"
        if loop == "discovery":
            # the console runs the first pass; the second pass needs the follow-up data
            m = re.search(r"## First pass\s*(.*?)(?=## Second pass|\Z)", txt, re.S)
            if m:
                txt = m.group(1).strip()
        # the team's prompts name files relative to the repository with Windows separators;
        # the server resolves paths against its own working directory, so give absolute ones
        txt = txt.replace("templates\\ArrowMarker_10x.jpg", str(repo / "templates" / "ArrowMarker_10x.jpg"))
        txt = re.sub(r"examples\\([A-Za-z0-9_]+)", lambda m: str(repo / "examples" / m.group(1)), txt)
        if loop == "navigation" and "load_template" not in txt and "template" not in txt.lower():
            # the particle filter needs the template loaded in the server session; the team's prompt assumes it
            # already is (the as-run session had it loaded by hand), so the console adds the step, in the open
            t = TEMPLATE_DEFAULTS
            step0 = (f"0. Load the navigation template {repo / 'templates' / 'ArrowMarker_10x.jpg'} with "
                     f"template_height_um {t['height_um']:.0f}, template_width_um 1433, origin_x_px {t['origin_x_px']}, "
                     f"origin_y_px {t['origin_y_px']}. Wait until it is loaded.\n")
            txt = re.sub(r"(Run all the tools following the order of the prompt:\s*\n)", lambda m: m.group(1) + step0, txt, count=1)
        out[loop] = txt
    return out


def skills_text() -> str:
    """The skills as one text, for a driver that has no skill mechanism (the system prompt carries them)."""
    parts = []
    for s in sorted(SKILLS.iterdir()) if SKILLS.is_dir() else []:
        f = s / "SKILL.md"
        if f.exists():
            body = re.sub(r"^---.*?---\s*", "", f.read_text(encoding="utf-8"), count=1, flags=re.S)
            parts.append(f"### Skill: {s.name}\n\n{body.strip()}")
    return "\n\n".join(parts)


def template_info(repo: Path) -> Optional[Dict[str, object]]:
    """The navigation template (templates/ArrowMarker_10x.jpg) and the geometry the particle filter uses:
    x_um = (px - origin_x) / s, y_um = -(py - origin_y) / s with s = height_px / height_um. The page draws the
    position estimate on this image; a load_template call in a run overrides the geometry for that run."""
    path = repo / "templates" / "ArrowMarker_10x.jpg"
    if not path.exists():
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    return dict(url="/api/template", path=str(path), width_px=w, height_px=h, **TEMPLATE_DEFAULTS)


def discovery_system_prompt(repo: Path) -> Optional[str]:
    skill = None
    for cand in (repo / "skills" / "mim-discovery-skill" / "SKILL.md",
                 repo / "discoveryLoopPacket" / "mim-discovery-skill" / "SKILL.md"):
        if cand.exists():
            skill = cand.read_text(encoding="utf-8")
            break
    if skill is None:
        return None
    return "The following skill is in effect for this task (mim_discovery_skill):\n\n" + skill


class ArchivedRun:
    """A run from an earlier console session, read back from its log directory (read-only)."""

    def __init__(self, d: Path):
        meta = json.loads((d / "meta.json").read_text())
        self.id, self.loop, self.model = meta["id"], meta["loop"], meta.get("model")
        self.prompt = meta.get("prompt", "")
        self.client_id = meta.get("client")
        self.dir = d
        self.started = d.stat().st_mtime
        self.events = []
        ev = d / "events.jsonl"
        if ev.exists():
            self.events = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()]
        res = d / "result.json"
        self.result = json.loads(res.read_text()) if res.exists() else None
        states = [e for e in self.events if e.get("type") == "status"]
        self.state = states[-1]["state"] if states else "unknown"
        if self.state in ("running", "connecting", "awaiting_approval"):
            self.state = "interrupted"

    def subscribe(self):
        q: asyncio.Queue = asyncio.Queue()
        for ev in self.events:
            q.put_nowait(ev)
        return q

    def unsubscribe(self, q):
        pass

    def decide(self, *a):
        return False

    async def inject(self, text):
        return False

    async def stop(self):
        return None


def load_archive(runs_dir: Path):
    for d in sorted(runs_dir.glob("*_*")):
        if (d / "meta.json").exists() and d.name not in RUNS:
            try:
                RUNS[d.name] = ArchivedRun(d)
            except Exception:
                pass


app = FastAPI(title="AIMS console")


class AccessToken:
    """Optional shared secret for the API (--access-token). The static page carries it inside its
    encrypted body, so only people with the page's password can start runs on this server. Checked
    on /api/* and /ws/* for both HTTP and WebSocket connections: header X-AIMS-Token, or ?token=
    for the WebSocket. CORS preflights pass, because the browser sends them without headers."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope["path"].startswith(("/api/", "/ws/")) \
                and scope["path"] != "/api/template" \
                and not (scope["type"] == "http" and scope["method"] == "OPTIONS"):
            given = dict(scope.get("headers") or {}).get(b"x-aims-token")
            if not given:
                from urllib.parse import parse_qs
                given = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [""])[0].encode()
            import hmac
            if not (given and hmac.compare_digest(given, self.token)):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4401})
                else:
                    body = b'{"detail":"this console needs its access token (the page carries it)"}'
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class StartRun(BaseModel):
    loop: str
    prompt: str
    model: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    max_turns: int = 200
    approve: bool = False
    handoff: Optional[str] = None
    client: Optional[str] = None
    use_skills: bool = True
    seed: Optional[int] = None          # the per-run simulator's seed (its start position); none = a random one


class Approve(BaseModel):
    approval_id: str
    decision: str
    note: str = ""


class Message(BaseModel):
    text: str


def visible(r, client: Optional[str]) -> bool:
    if not CFG.get("public"):
        return True
    return bool(client) and getattr(r, "client_id", None) == client


def live_runs() -> int:
    return sum(1 for r in RUNS.values() if isinstance(r, BaseRun) and r.state in ("created", "connecting", "running", "awaiting_approval"))


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
def config():
    return {k: v for k, v in CFG.items() if k not in ("env", "mcp_dir", "sim_script", "examples", "access_token")}


@app.get("/api/template")
def template_image():
    t = CFG.get("template")
    if not t:
        return JSONResponse({"error": "no template"}, status_code=404)
    return FileResponse(t["path"], media_type="image/jpeg")


@app.get("/api/tools")
async def tools():
    global TOOLS_CACHE
    if TOOLS_CACHE:
        return TOOLS_CACHE
    if not CFG.get("mcp_url"):
        return {"ok": False, "error": "no shared MCP server; per-run servers start with each run", "tools": []}
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    try:
        async with sse_client(str(CFG["mcp_url"])) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                t = await s.list_tools()
                return {"ok": True, "tools": [{"name": x.name, "description": (x.description or "")[:200]} for x in t.tools]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "tools": []}


@app.get("/api/runs")
def list_runs(client: Optional[str] = Query(default=None)):
    return [{"id": r.id, "loop": r.loop, "state": r.state, "model": r.model, "started": r.started, "result": r.result}
            for r in sorted(RUNS.values(), key=lambda r: r.started, reverse=True) if visible(r, client)]


@app.post("/api/runs")
async def start_run(req: StartRun):
    public = bool(CFG.get("public"))
    if req.loop not in LOOP_FILES and req.loop != "custom":
        raise HTTPException(400, f"unknown loop {req.loop}")
    model = (req.model or str(CFG["model"])).strip()
    provider = req.provider or provider_for(model)
    api_key = (req.api_key or "").strip() or None
    if public:
        if req.loop == "discovery":
            raise HTTPException(403, "the public console runs the navigation and measurement loops on the simulator; "
                                     "the discovery loop (file and shell tools) runs with the downloadable console")
        if not api_key and not (CFG.get("hosted") and provider == "anthropic"):
            raise HTTPException(400, "this console runs on your own API key: enter an Anthropic or OpenAI key"
                                if not CFG.get("hosted") else "GPT models need your own OpenAI key here; the hosted key is an Anthropic key")
        if live_runs() >= int(CFG["max_concurrent"]):
            raise HTTPException(429, f"{CFG['max_concurrent']} runs are already in progress; try again in a few minutes")
        req.max_turns = min(req.max_turns, int(CFG["max_turns_cap"]))
        if not req.client:
            raise HTTPException(400, "missing client id")
    if provider == "openai" and req.loop == "discovery":
        raise HTTPException(400, "the discovery loop runs only with the Claude driver (file and shell tools)")
    if provider == "openai" and not api_key:
        raise HTTPException(400, "a GPT model needs an OpenAI API key for the run")
    prompt = req.prompt
    if req.handoff:
        prompt = f"Hand-off note from the previous loop (written by the operator):\n{req.handoff.strip()}\n\n{prompt}"
    runs_dir = Path(str(CFG["runs_dir"]))
    use_skills = bool(req.use_skills and CFG.get("skills"))
    wall = float(CFG["wall_limit_min"]) * 60 if public else None
    common = dict(loop=req.loop, prompt=prompt, model=model, max_turns=req.max_turns, log_dir=runs_dir,
                  env=CFG.get("env"), api_key=api_key, client=req.client, wall_limit_s=wall)

    if req.loop == "discovery":
        packet = CFG.get("packet")
        if not packet:
            raise HTTPException(400, "discovery loop needs --packet (a discoveryLoopPacket directory)")
        # the agent works in a fresh copy of the packet (no analysis_results), never in the original
        run = ClaudeRun(system_prompt=CFG.get("discovery_system_prompt"), mcp_url=None, cwd=None, approve=False,
                        approve_tools=None, tools_mode="files", **common)
        work = run.dir / "packet"
        shutil.copytree(str(packet), work, ignore=shutil.ignore_patterns("analysis_results", "analysis_results_pass2", "__pycache__"))
        run.cwd = str(work)
    else:
        # the instrument: one shared server, or a private simulated one for this run
        per_run = bool(CFG.get("sim_per_run"))
        # the recovery skills are steps of the navigation protocol: offered there only, never written into the prompt
        nav_skills = use_skills and req.loop == "navigation"
        skills_dir = SKILLS if nav_skills else None
        kw = dict(system_prompt=system_prompt_for(req.loop, nav_skills, req.prompt), mcp_url=None if per_run else str(CFG["mcp_url"]), cwd=None,
                  approve=req.approve, approve_tools=None, tools_mode="mcp", skills_dir=skills_dir,
                  extra_meta={"provider": provider}, **common)
        if provider == "openai":
            run = OpenAIRun(skills_text=skills_text() if nav_skills else None, **kw)
        else:
            run = ClaudeRun(**kw)
        work = run.dir / "work"
        work.mkdir(exist_ok=True)
        install_skills(work, skills_dir)
        run.cwd = str(work)
        if per_run:
            simdir = run.dir / "sim"
            seed = req.seed if req.seed is not None and 0 <= req.seed < 2 ** 31 else None
            sim = SimServer(sim_script=Path(str(CFG["sim_script"])), mcp_dir=Path(str(CFG["mcp_dir"])), sessions_dir=simdir / "sessions",
                            log_path=simdir / "sim_server.log", python=os.environ.get("PYTHON") or None, seed=seed,
                            p_defect=float(CFG.get("p_defect", 0.3)), extra_args=sim_args() + ["--agent-cwd", str(work)])
            try:
                await sim.start()
            except Exception as e:  # noqa: BLE001
                sim.stop()
                run.emit({"type": "error", "message": f"simulated instrument did not start: {e}"})
                run.set_state("error", "simulator")
                RUNS[run.id] = run
                raise HTTPException(503, f"could not start a simulated instrument for this run: {e}")
            run.mcp_url = sim.url
            run.update_meta(mcp_url=sim.url, simulator_seed=sim.seed, simulator_port=sim.port, p_defect=sim.p_defect,
                            couple_controller=bool(CFG.get("couple_controller")), skills=install_names(skills_dir),
                            handoff=req.handoff or None)
            run.on_finish.append(sim.stop)
    RUNS[run.id] = run
    await run.start()
    return {"id": run.id, "driver": run.driver, "model": model}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, client: Optional[str] = Query(default=None)):
    r = RUNS.get(run_id)
    if not r or not visible(r, client):
        raise HTTPException(404)
    return {"id": r.id, "loop": r.loop, "state": r.state, "model": r.model, "result": r.result,
            "prompt": r.prompt, "n_events": len(r.events), "archived": isinstance(r, ArchivedRun)}


@app.get("/api/runs/{run_id}/events.jsonl")
def get_events(run_id: str, client: Optional[str] = Query(default=None)):
    r = RUNS.get(run_id)
    if not r or not visible(r, client):
        raise HTTPException(404)
    p = r.dir / "events.jsonl"
    return PlainTextResponse(p.read_text() if p.exists() else "", media_type="application/x-ndjson")


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, req: Approve, client: Optional[str] = Query(default=None)):
    r = RUNS.get(run_id)
    if not r or not visible(r, client):
        raise HTTPException(404)
    ok = r.decide(req.approval_id, "allow" if req.decision == "allow" else "deny", req.note)
    return {"ok": ok}


@app.post("/api/runs/{run_id}/message")
async def message(run_id: str, req: Message, client: Optional[str] = Query(default=None)):
    r = RUNS.get(run_id)
    if not r or not visible(r, client):
        raise HTTPException(404)
    return {"ok": await r.inject(req.text)}


@app.post("/api/runs/{run_id}/stop")
async def stop(run_id: str, client: Optional[str] = Query(default=None)):
    r = RUNS.get(run_id)
    if not r or not visible(r, client):
        raise HTTPException(404)
    await r.stop()
    return {"ok": True}


@app.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: str, client: Optional[str] = Query(default=None)):
    await ws.accept()
    r = RUNS.get(run_id)
    if not r or not visible(r, client):
        await ws.send_text(json.dumps({"type": "error", "message": "no such run"}))
        await ws.close()
        return
    q = r.subscribe()
    try:
        while True:
            ev = await q.get()
            await ws.send_text(json.dumps(ev))
    except WebSocketDisconnect:
        pass
    finally:
        r.unsubscribe(q)


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


def sim_args() -> List[str]:
    """Template and examples for a per-run simulator, from the console's configuration rather than the mcpServer directory's neighbours."""
    args: List[str] = []
    t = CFG.get("template")
    if t:
        args += ["--template", str(t["path"])]
    if CFG.get("examples"):
        args += ["--examples", str(CFG["examples"])]
    if CFG.get("runs_dir"):
        # One GP regression peaks at about 5.4 GB. The per-run simulators share this lock
        # file, so only one regression computes at a time across the console's runs (threaded_tools.py).
        args += ["--heavy-lock", str(Path(str(CFG["runs_dir"])) / ".heavy_tools.lock")]
    if CFG.get("couple_controller"):
        # a scan with the tip-retraction defect leaves the Z controller off, so step 3 of the navigation prompt (or the
        # aims-controller-recovery skill) has something to do, as in the navigation rule test and the page's run
        args.append("--couple-controller")
    return args


def install_names(skills_dir: Optional[Path]) -> List[str]:
    """The skills a run gets, by name (none when the skills are off or the loop has none)."""
    if not skills_dir or not skills_dir.is_dir():
        return []
    return sorted(p.name for p in skills_dir.iterdir() if (p / "SKILL.md").exists())


async def prime_tools_cache():
    """In per-run mode, start one simulator briefly for the tool list shown in section 06."""
    global TOOLS_CACHE
    sims = Path(str(CFG["runs_dir"])) / "_tools_probe"
    sim = SimServer(sim_script=Path(str(CFG["sim_script"])), mcp_dir=Path(str(CFG["mcp_dir"])), sessions_dir=sims / "sessions",
                    log_path=sims / "sim_server.log", python=os.environ.get("PYTHON") or None, extra_args=sim_args())
    try:
        await sim.start()
        t = await sim.list_tools()
        TOOLS_CACHE = {"ok": True, "tools": [{"name": x.name, "description": (x.description or "")[:200]} for x in t]}
    except Exception as e:  # noqa: BLE001
        TOOLS_CACHE = {"ok": False, "error": str(e), "tools": []}
    finally:
        sim.stop()
        shutil.rmtree(sims, ignore_errors=True)


@app.on_event("startup")
async def _startup():
    if CFG.get("sim_per_run"):
        asyncio.create_task(prime_tools_cache())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="the AIMS repository (prompts/, templates/, skills/, mcpServer/)")
    ap.add_argument("--mcp-url", default=None, help="a shared MCP server (real instrument or one simulator); default http://localhost:8000/sse")
    ap.add_argument("--sim", action="store_true", help="label the session as simulated (start_server_sim.py)")
    ap.add_argument("--sim-per-run", action="store_true", help="start a private simulated MCP server for every run")
    ap.add_argument("--mcp-dir", default=None, help="the team's mcpServer directory for per-run simulators (default <repo>/mcpServer)")
    ap.add_argument("--p-defect", type=float, default=0.3, help="simulator: tip-retraction defect probability per scan")
    ap.add_argument("--examples", default=None, help="examples directory for the simulator's replays (default <repo>/examples)")
    ap.add_argument("--model", default="claude-opus-4-5", help="default model (the paper's navigation/measurement model)")
    ap.add_argument("--packet", default=None, help="discoveryLoopPacket directory for the discovery loop")
    ap.add_argument("--runs", default="runs_console", help="where run logs go")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--public", action="store_true", help="bring-your-own-key mode: per-browser runs, caps, no discovery loop")
    ap.add_argument("--hosted", action="store_true", default=os.environ.get("AIMS_HOSTED") == "1",
                    help="with --public: runs may use the service's own Anthropic key (the authors pay, within the caps); "
                         "the page then asks for no key. Use behind an access token only. Also AIMS_HOSTED=1.")
    ap.add_argument("--origins", default="https://leonmgarcia.com,http://127.0.0.1:8080,http://localhost:8080",
                    help="comma-separated browser origins allowed to call the API (the static page's host)")
    ap.add_argument("--max-concurrent", type=int, default=4)
    ap.add_argument("--max-turns-cap", type=int, default=80)
    ap.add_argument("--wall-limit-min", type=float, default=45)
    ap.add_argument("--no-skills", action="store_true", help="do not offer the recovery skills (prompt rules only)")
    ap.add_argument("--no-couple-controller", action="store_true",
                    help="per-run simulators: a scan with the tip-retraction defect leaves the Z controller on (the default "
                         "before 2026-11-14), so step 3 of the navigation prompt never has anything to do")
    ap.add_argument("--released-prompts", action="store_true",
                    help="on the simulator, show the prompts exactly as released (50 um stop, measurement blanks unfilled)")
    ap.add_argument("--access-token", default=os.environ.get("AIMS_ACCESS_TOKEN") or None,
                    help="shared secret the page must send with every API call (or env AIMS_ACCESS_TOKEN); "
                         "build the static page with the same --token so only its password holders reach this server")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    mcp_url = a.mcp_url or (None if a.sim_per_run else "http://localhost:8000/sse")
    prompts = load_prompts(repo)
    skills = not a.no_skills and SKILLS.is_dir()
    sim = bool(a.sim or a.sim_per_run or os.environ.get("AIMS_SIM") == "1")
    # the prompts are the team's whether or not the skills are on (prompts_plain is kept for pages built before
    # 2026-11-13); on the simulator two of them are adapted in the open (simulator_prompts, SIM_NOTES)
    shown, notes = simulator_prompts(prompts) if sim and not a.released_prompts else (dict(prompts), {})
    prompts_skills = shown
    CFG.update(prompts_released=prompts, prompt_notes=notes, objective_rules=OBJECTIVE_RULES,
               prompts_objective={"navigation": objective_prompt(repo, SIM_STOP_UM if sim and not a.released_prompts else 50)},
               objective_notes={"navigation": OBJECTIVE_NOTE},
               sim_handoff=SIM_HANDOFF if sim and not a.released_prompts else {},
               couple_controller=bool(a.sim_per_run and not a.no_couple_controller))
    CFG.update(repo=str(repo), mcp_url=mcp_url, sim=sim,
               sim_per_run=bool(a.sim_per_run), mcp_dir=str(Path(a.mcp_dir).resolve() if a.mcp_dir else repo / "mcpServer"),
               sim_script=str(ROOT / "sim" / "start_server_sim.py"), p_defect=a.p_defect,
               examples=str(Path(a.examples).resolve() if a.examples else repo / "examples"),
               model=a.model, models=MODELS, packet=str(Path(a.packet).resolve()) if a.packet else None,
               runs_dir=str(Path(a.runs).resolve()), prompts=prompts_skills, prompts_plain=prompts,
               discovery_system_prompt=discovery_system_prompt(repo), loops=list(LOOP_FILES), prompt_files=LOOP_FILES,
               operating_rules=OPERATING_RULES, skills_rule=SKILLS_RULE, template=template_info(repo), skills=skills,
               skill_names=sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").exists()) if skills else [],
               public=bool(a.public), hosted=bool(a.public and a.hosted), max_concurrent=a.max_concurrent,
               max_turns_cap=a.max_turns_cap, wall_limit_min=a.wall_limit_min, byok=bool(a.public and not a.hosted),
               # the agent subprocess gets a scrubbed environment: credentials and proxy only
               env={k: v for k, v in os.environ.items() if k in (
                   "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                   "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
                   "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS", "PATH", "HOME",
                   "TERM", "LANG")})
    if a.public and not a.hosted:   # a public server never lends its own credentials to a run
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            CFG["env"].pop(k, None)
    if a.public and a.hosted:
        if not any(CFG["env"].get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")):
            sys.exit("--hosted needs the service's Anthropic credential in the environment (ANTHROPIC_API_KEY)")
        if not a.access_token:
            sys.exit("--hosted lends the service's key to every caller: it needs --access-token / AIMS_ACCESS_TOKEN")
    Path(str(CFG["runs_dir"])).mkdir(parents=True, exist_ok=True)
    load_archive(Path(str(CFG["runs_dir"])))
    origins = [o.strip() for o in a.origins.split(",") if o.strip()]
    if a.access_token:
        CFG["access_token"] = a.access_token
        app.add_middleware(AccessToken, token=a.access_token)
    # CORS outermost, so a 401 from the token check still reaches the page as a readable answer
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])
    import uvicorn
    print(f"AIMS console on http://{a.host}:{a.port}  mode={'SIMULATION' if CFG['sim'] else 'HARDWARE'}  "
          f"mcp={mcp_url or 'per run'}  model={a.model}  public={a.public}  skills={skills}  "
          f"hosted={bool(a.public and a.hosted)}  access_token={'set' if a.access_token else 'none'}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
