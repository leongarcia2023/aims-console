# AIMS console

A dedicated interface for the three AIMS loops, separate from any chat client. The console
runs the navigation, measurement and discovery loops with the team's own prompts, calls the
MCP tools directly through an agent subprocess, shows every step as it happens (tool call,
arguments, result, images), holds instrument actions for operator approval when asked, makes
the hand-off between loops an explicit, logged operator step, and writes a complete event log
per run. A simulation mode runs the whole stack without the microscope.

Built on the Stanford AIMS repository (`mcpServer/`, `prompts/`, `templates/`, `examples/`)
without modifying it. `MANUAL.md` is the hands-on guide for a new user; this file is the
technical reference; `deploy/DEPLOY.md` is the recipe for the public service. First version
2026-11-05, page restyled 2026-11-06, public mode (bring your own key, model choice, the two
recovery skills, GPT driver) 2026-11-07; written to be merged with Siyuan's console prototype
rather than replace it (see Merging).

## Layout

```
aims_console/
  sim/start_server_sim.py   the team's MCP server on a simulated instrument (same SSE endpoint)
  sim/sim_hardware.py       virtual stage + synthetic navigation scans (.sxm) + replay of recorded gate
                            sweeps and grid spectra; ground truth logged to the session's truth.jsonl
  backend/app.py            FastAPI service: API, WebSocket event stream, run logs, public mode, access token
  backend/runner.py         BaseRun (events, approvals, watchdog) and ClaudeRun (Claude Agent SDK)
  backend/openai_runner.py  OpenAIRun: a GPT model on the same MCP tools through the Responses API
  backend/simpool.py        one simulated MCP server per run, on a loopback port, stopped with the run
  skills/                   aims-controller-recovery, aims-gp-recovery: steps 3 and 7 of the navigation prompt
  web/index.html, app.js, style.css   the console page (no build step); web/fonts/ holds Latin Modern (GUST licence)
  static/build_static.py    a password-gated static copy: replays recorded runs, or drives a public service (gate.html, access.js)
  deploy/                   Dockerfile, docker-compose.yml, Caddyfile, DEPLOY.md for the public service
  requirements.txt          console + the team's server requirements (without the hardware libraries)
  MANUAL.md                 the user guide
  run_console.sh            starts the service under a clean environment
  test/smoke_sim.py         walks the instrument tools over MCP against the simulator
```

## Running

Two processes: the MCP server (real or simulated) and the console.

```bash
# 1a. real instrument: the team's server as usual
cd AIMS/mcpServer && python start_server_sse.py 8000
# 1b. or the simulator (nothing in the repository is modified)
python aims_console/sim/start_server_sim.py --mcp-dir AIMS/mcpServer --port 8000 --seed 0 --p-defect 0.3

# 2. the console (needs the claude CLI on PATH and either ANTHROPIC_API_KEY or a signed-in CLI)
pip install fastapi uvicorn websockets claude-agent-sdk mcp
./aims_console/run_console.sh --repo /path/to/AIMS --mcp-url http://localhost:8000/sse [--sim] \
    --model claude-opus-4-5 --packet /path/to/discoveryLoopPacket --port 8080
# open http://127.0.0.1:8080
```

`run_console.sh` passes only credentials, proxy settings, PATH and the `AIMS_*` variables to
the service, so the agent subprocesses inherit nothing else (no chat-client project context,
memory or extra MCP servers; the discovery reruns found exactly that kind of leakage). The
service binds to 127.0.0.1; put it behind SSH or a VPN rather than on an open port, because
approving a stage move is an instrument action.

### Running it on your own machine (reviewers)

`reviewer.sh` starts the console in public mode on `127.0.0.1:8080` with a virtualenv it creates
on first use; `install.sh` is the one-line version that clones the published repository first.
`Dockerfile` at the repository root bakes the console, the AIMS instrument code and the recorded
example sweeps into one image (`docker run --rm -p 8080:8080 ghcr.io/leongarcia2023/aims-console`),
built by `.github/workflows/image.yml`. Both are bring-your-own-key: the key is entered in the
page, lives in the run's process, and is never written to disk. This is how the paper's reviewers
are meant to run it; nothing is hosted by the authors.

### Public mode (also the hosted form, if ever wanted)

```bash
AIMS_ACCESS_TOKEN=<secret> ./run_console.sh --repo /path/to/AIMS --examples /path/to/examples \
    --sim-per-run --public --origins https://leonmgarcia.com --host 0.0.0.0 --port 8080
```

This is the mode behind `leonmgarcia.com/AIMS` (`deploy/DEPLOY.md` has the Docker recipe). It
exists so a reviewer can do their own runs rather than watch recorded ones:

* `--sim-per-run`: every run starts its own simulated MCP server (`backend/simpool.py`) on a
  loopback port, with its sessions under the run's directory, and stops it at the end. Runs
  never share instrument state, and no shared server needs to be running.
* `--public`: bring your own key. The page asks for an Anthropic or OpenAI key; the key travels
  with the run request, lives in that run's process environment (Claude) or request headers
  (OpenAI), and is never written to disk or logged (`meta.json` keeps `own_key: true`). The
  server's own credentials are stripped from the environment at startup. Runs are visible only
  to the browser that started them (a client id). Caps: `--max-turns-cap` 80,
  `--wall-limit-min` 45, `--max-concurrent` 4. The discovery loop is not offered, since it
  needs file and shell tools on the server.
* `--access-token` (or `AIMS_ACCESS_TOKEN`): a shared secret required on every API call and
  WebSocket (`X-AIMS-Token` header, `?token=` for the socket). The static page is built with
  the same value (`build_static.py --token`) inside its encrypted body, so only people with the
  page's password reach the service. `/api/template` (the public template image) is exempt.
* `--origins`: the browser origins allowed by CORS. The token, not CORS, is the lock.

### Models

The page offers the models of the study and a free field: Claude Opus 4.5 (navigation and
measurement in the paper), Claude Opus 4.8 (discovery and the contamination reruns), GPT-5.5
(the paper's benchmark), Claude Opus 5.5 (the second-model check), and Sonnet and Haiku 5.5
for cheap tries. The driver follows the provider: Claude models run through the Claude Agent
SDK, GPT models through `backend/openai_runner.py`, which lists the MCP server's tools, hands
them to the Responses API as function tools, executes the calls over MCP, feeds results and
images back, and honours approvals, operator messages and Stop exactly like the Claude driver.
Both drivers emit the same events, so the page and the logs do not know which one ran. The
catalog is `MODELS` in `backend/app.py`.

### The two recovery skills

Steps 3 and 7 of the team's navigation prompt are conditional recoveries (controller off:
retry the approach with 100 nm more lift; uncertainty above 53 µm: GP-reconstruct the scan and
predict again). With "Recovery steps as skills" ticked (the default), those two lines are
replaced by one-line instructions to use `aims-controller-recovery` and `aims-gp-recovery`, and
the skills themselves (`skills/*/SKILL.md`, copied into the run's working directory as
`.claude/skills/`) carry the procedures. The Claude driver loads them through the SDK's project
settings source and the agent invokes them with the Skill tool (visible in the ledger as
`Skill aims-controller-recovery`); the GPT driver appends the skill texts to the system prompt,
since the Responses API has no skill mechanism. Unticked, the prompt spells the steps out as
before, which is the paper's configuration. Verified on 2026-11-07 in a live simulator run:
both skills were invoked at the right steps.

## What the page does

The page is laid out like a paper, not a dashboard: white page, Computer Modern type (Latin
Modern, bundled in `web/fonts/` under the GUST font licence; CMU Serif is used when it is
installed), 2 px ink rules, no rounded corners, numbered sections. Colours follow
`plotstyle_5.mplstyle`: steel for navigation, terracotta for measurement, teal for discovery,
rust for things that need the operator. The instrument panel draws the particle-filter
estimate as a small plot in the same idiom (ticks in, all four spines, one-sigma ellipse,
the scan frame as a dashed square, the target as a diamond at the origin).

* The three loops are a process chain with the hand-off arrows between them; choosing a step
  loads the team's prompt (`prompts/*.md`) with the repository paths made absolute (the server
  resolves paths against its own working directory) and, for navigation, a step 0 that loads
  the template, because the particle filter needs it and the team's prompt assumes it is
  already loaded. The operator can edit the text before starting; the discovery step runs the
  first pass in a fresh copy of the packet.
* The stage map draws the particle-filter estimates on the navigation template itself
  (`/api/template` serves `templates/ArrowMarker_10x.jpg`; the geometry is the one the filter
  uses, origin pixel and height in µm, taken from the run's own `load_template` call when there
  is one). Two views: the whole template, and a close-up around the target. Scans are shown
  square whatever their pixel grid, because they cover a square frame.
* Start / Stop, model and turn cap per run.
* "Hold instrument actions for approval": calls to `move_tip`, `execute_movement`,
  `approach_and_lift`, `retract_tip`, `scan`, the gate ramps, the sweepers, `nanonis_xy_set`
  and `set_scan_frame` pause the agent until the operator allows or denies (with a note the
  agent sees). Analysis tools run without a prompt. With the box unchecked the agent runs the
  protocol unattended, as in the paper.
* Step console: assistant text, each tool call with its arguments, each result with images
  (scans, particle-filter plots) inline, approvals and operator messages, the final result with
  turns and cost. The right panel tracks the latest image, the position estimate, uncertainty,
  confidence and distance from the particle filter, the last stage move, the bottom-gate
  voltage and the scan frame.
* Message box: an operator message delivered as the agent's next turn.
* Hand-off: when a loop finishes, "Use as the hand-off note for the next loop" copies its
  final message into the hand-off field and selects the next step of the chain; the operator
  edits the note and starts the next loop with it prepended. The note and the decision are in
  the log, which is what the paper's reframing ("two automated loops and one, with a manual
  hand-off") needs to show.
* Log: `runs_console/<run id>/events.jsonl` (every event with a timestamp), `meta.json` (loop,
  prompt, model, settings), `result.json`; downloadable from the page.

## How it drives the agent

`backend/runner.py` uses the Claude Agent SDK (the same headless Claude Code engine the
reruns used): the MCP server is the agent's only tool surface for navigation and measurement
(`mcp_servers={"aims": sse}`, `strict_mcp_config`, Bash/Write/Edit/Web tools disallowed; the
Skill tool only when the skills are on), file and shell tools only for discovery,
`setting_sources=["project"]` only to pick up the run's own `.claude/skills`, a private HOME
per run (nothing of the operator's account but, on the lab machine, the signed-in CLI's
credentials), and a `can_use_tool` callback for approvals. Everything the agent sees and does
comes back as typed messages and is turned into the event stream. `backend/openai_runner.py`
is the second driver on the same contract, so the page and logs do not change with the model.

## The simulator

`sim_hardware.SimHardwareManager` replaces `hardware_connections.HardwareManager` inside the
unmodified server (`start_server_sim.py` rebinds the name before importing `mcp_server_sse`).

* Navigation: a virtual stage over the real template (`ArrowMarker_10x.jpg`, origin pixel
  (867, 498), 0.8755 um/px). `scan` renders the 53 um MIM-like image at the true position
  (template crop, actuator and row noise, a tip-retraction defect with probability `p_defect`)
  and writes a genuine Nanonis `.sxm` (17 channels, both directions) that pySPM and the
  server's `get_sxm_image`, `convert_sxm_to_png`, `run_gp_regression` and the particle filter
  read unchanged. `move_tip` / `execute_movement` move the stage with the configured
  microns-per-step and actuator error. Start position: `--init-r` (random direction) or
  `--start-xy`.
* Measurement: with a scan frame of 10 um or less, `scan` and `bottom_gate_ramp_and_scan`
  replay the recorded `examples/VbgScans` image nearest to the requested V_bg; the generic
  sweeper replays `examples/GridSpectra` in order; `analyze_sxm_dynamics`,
  `analyze_twist_angle_disorder`, SSIM and GWC tools then work on real device data.
* Ground truth (true position after every scan and move, the defect flag, the replayed file)
  goes to `sessions/sim/<stamp>/truth.jsonl` in the server directory; the agent never sees it.
  It is what an evaluation of the navigation policy against the truth needs.
* Not modelled: approach physics (always succeeds), acquisition time (`--scan-seconds`),
  temperature, and any dependence of the replayed spectra on the position inside the
  measurement frame.
* One server detail the simulator works around: `get_sxm_image` builds the file path with a
  literal backslash (`session_path + "\\name.sxm"`), which is a filename on Linux and macOS.
  The simulator writes a second hard link under that name so the tool finds the file; the real
  fix is `os.path.join` in the server, one line.

Tested end to end on 2026-11-05 against the simulator: the full tool chain over MCP
(`test/smoke_sim.py`), a navigation run through the console with approvals (template load,
session, approach, scan, image, particle filter at 126 s per prediction on 2 cores, movement
calculation, `execute_movement`, summary; 9 turns, $0.009 on Haiku), and a discovery run in a
packet copy. The test evidence is in `test/` (server and console logs, `console_screenshot.png`,
`sim_vs_real_scans.png`).

## Merging with Siyuan's prototype

Siyuan's page is a console-style view of each step that calls the MCP tools directly. Three
ways to combine, in order of preference:

1. Keep his page as the front end and point it at this service's API and WebSocket
   (`/api/runs`, `/ws/runs/{id}`, `/api/runs/{id}/approve`): the event contract is in
   `backend/runner.py`'s docstring. His page gains the agent loop, approvals, hand-off and logs
   without re-implementing them.
2. Keep this page and drop his step renderer in where `addStep` is, if his rendering is better.
3. Run both: his page for direct tool calls (manual operation), this one for the loops.

What his zip needs to tell us: the front-end stack, how it reaches the MCP server (direct SSE
client in the browser, or through a small server), and how the model is called today (API key,
or through LibreChat).

## Open items

* A "ground truth" overlay for simulation runs (true position next to the estimate) can be
  added by having the console read the server's `truth.jsonl`; it is a file read away.
* Hardware runs should be exercised with approvals on first, with Siyuan at the instrument.
* No hosted service (decided 2026-11-08): reviewers run the console themselves. `deploy/` keeps
  the hosted recipe (Docker, Caddy, bootstrap) in case one is ever wanted. The page at
  `leonmgarcia.com/AIMS` replays the recorded runs and points at a local console.
* The GPT driver has been exercised against a mock of the Responses API and a real MCP server,
  not yet against OpenAI itself; the first real run should be watched.
