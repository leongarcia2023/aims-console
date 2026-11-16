# AIMS console: guide for editors and reviewers

The AIMS console runs the navigation loop and the measurement loop of AIMS on a simulated
microscope, on your own API key, and records every step the agent takes. You can watch the agent
work through the team's own instrument tools, change its prompt and see what changes, and compare
its position estimates with the simulator's ground truth, which the agent never sees.

This guide follows the Nature Portfolio code and software submission checklist: system
requirements (section 2), installation (3), a demo with its expected output (4 and 5),
instructions for use (6) and how to reproduce the reported runs (7).

**Quick start** (about 15 minutes, about $0.40):

1. Have Docker with 7 GB of memory for containers, or git and Python 3.11 or 3.12.
2. Run `docker run --rm -p 8080:8080 ghcr.io/leongarcia2023/aims-console`, or without Docker
   `curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/install.sh | bash`.
3. Open <http://localhost:8080>, keep the model **Claude Opus 4.5**, paste an Anthropic API key,
   click **Measurement** and press **Start run**. Section 4 says what you should see.

## 1. What the console does, and what it does not

- It runs **loop 1 (navigation)** and **loop 2 (measurement)** of the paper with the team's MCP
  server code (`aims/mcpServer/`), unmodified, on a simulated instrument. The stage, the scan
  images and the gate and grid data are simulated or replayed; the tools that act on them (the
  particle filter, GP regression, region selection, the twist-angle and GWC analyses) are the
  team's own.
- Every run gets its own simulated instrument. The console records every message of the agent,
  every tool call with its arguments, every result and every image.
- Navigation offers two prompts. The default, **Objective only, uncertainty as skills**, gives the
  agent the goal and the fixed parameters of the team's prompt with no rules, and offers two
  skills (an interrupted scan, an uncertain position estimate) that the agent may open when it
  recognizes the situation. Unticked, the agent gets the team's prompt with the recovery rules
  written in, as in the paper's runs.
- It does not run the **discovery loop**, which needs file and shell tools and the discovery
  packet; the scripts of the blinded discovery reruns are in `rerun_kit/` (see its README). It
  does not drive the real microscope: that mode is for the lab.
- Four recorded runs can be replayed in the browser at
  [leonmgarcia.com/AIMS](https://leonmgarcia.com/AIMS): navigation with each of the two prompts, a
  measurement run and a discovery run. The password is given to the editor.

## 2. System requirements

| | |
|---|---|
| Model access | An Anthropic API key for Claude models (the tested path; the paper's loops 1 and 2 used Claude Opus 4.5). GPT models need an OpenAI key; the GPT driver is included but has been run only against a mock of OpenAI's API. You pay your provider for the runs (sections 4 and 5 give typical costs). |
| Software | Either Docker (Docker Desktop on macOS or Windows, Docker Engine on Linux; the image is built for linux/amd64 and linux/arm64, so Apple silicon runs it natively), or git, bash and Python 3.11 or 3.12 on macOS or Linux (on Windows, inside WSL). Newer Pythons are refused because pySPM, which reads the microscope's files, does not build on them; on macOS, `brew install python@3.12`. All Python dependencies are in `aims_console/requirements.txt`. |
| Memory | 7 GB free. One GP regression peaks at about 5.4 GB; with less than 6 GB the simulated instrument is killed during a navigation run. In Docker Desktop on macOS, raise the memory limit under Settings, Resources to 7 GB or more; on Windows, Docker uses the memory WSL 2 is given, by default half of the computer's. |
| Disk | About 1 GB for the Python environment (the Docker image holds the same packages). |
| Hardware | No GPU or special hardware. The times below are for two CPU cores. |
| Network | Your model provider, and GitHub, PyPI or the GitHub container registry to install. |
| Browser | Any current browser. The page loads nothing from outside your computer. |
| Tested | Linux x86_64, Python 3.12.3, two cores, 8 GB, a fresh clone on 16 November 2026 (installation without Docker, the page, the simulator check of section 3 and the key handling of section 6). Navigation and measurement runs with Claude Opus 4.5 on 14 November 2026 (sections 4 and 5). |

## 3. Installation

### With Docker

```bash
docker run --rm -p 8080:8080 ghcr.io/leongarcia2023/aims-console
```

Open <http://localhost:8080>. Ctrl-C stops the console. The run records live inside the container
and go with it; to keep them, start it with a name and copy them out before stopping it:

```bash
docker run --rm --name aims -p 8080:8080 ghcr.io/leongarcia2023/aims-console
docker cp aims:/runs ./aims-runs        # in a second terminal, while it runs
```

Options for the console go after the image name and replace the default `--max-concurrent 2`,
for example `... ghcr.io/leongarcia2023/aims-console --max-concurrent 2 --max-turns-cap 200 --wall-limit-min 75`.

### Without Docker

```bash
curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/install.sh | bash
```

This clones the repository into `./aims-console-local`, creates a Python environment in
`aims-console-local/aims_console/.venv` (once; 39 seconds on a fast connection without a
package cache, about 1 GB), starts the console and opens <http://localhost:8080>. Ctrl-C stops
it. Later:

```bash
./aims-console-local/aims_console/reviewer.sh                       # start again
PORT=8090 ./aims-console-local/aims_console/reviewer.sh             # another port
./aims-console-local/aims_console/reviewer.sh --max-turns-cap 200 --wall-limit-min 75   # options
PYTHON=/path/to/python3.12 ./aims-console-local/aims_console/reviewer.sh                # a given Python
```

Run records are written to `aims-console-local/aims_console/runs_local/<run id>/`.

### Check the installation without an API key (optional, about 3 minutes)

This drives the simulated instrument through the team's tools directly, with no model. In two
terminals, both in `aims-console-local/aims_console/`:

```bash
# terminal 1: the simulated instrument (Ctrl-C stops it when the check is done)
.venv/bin/python sim/start_server_sim.py --mcp-dir ../aims/mcpServer --port 8765 --bind 127.0.0.1

# terminal 2, once terminal 1 shows "Uvicorn running"
.venv/bin/python test/smoke_sim.py --url http://127.0.0.1:8765/sse --template "$PWD/../aims/templates/ArrowMarker_10x.jpg"
```

The second command prints each tool's answer: the template loads, the particle filter returns a
position (one to three minutes), the stage moves, a gate ramp-and-scan and a grid sweep run. The
template path must be absolute.

## 4. Demo: one measurement run (about 9 minutes, about $0.40)

1. Open <http://localhost:8080>. The note under the title says that every run gets its own
   simulated instrument and runs on the key you enter.
2. Under **02 Settings**, keep the model **Claude Opus 4.5 (anthropic)** and paste your Anthropic
   key.
3. Under **01 Loop**, click **Measurement**. The prompt box shows the team's measurement prompt
   with its blanks filled for the replayed device data; the hand-off field holds the device's scan
   frame, the note a navigation run would hand over.
4. Press **Start run**. The run appears under **05 Runs**, and its record fills **03 Run record**.

**Expected output.** Eight tool calls, in this order: `set_scan_frame` (the device, 3 × 3 µm),
`bottom_gate_ramp_and_scan` (2.5 to 3.4 V in 10 steps), `analyze_sxm_dynamics` (returns a
2 × 0.5 µm region), `set_scan_frame` (that region), `grid_generic_sweeper_start` (a 12 × 3 grid,
two iterations), `analyze_twist_angle_disorder`, `evaluate_fractional_gwc_points` and a last
`grid_generic_sweeper_start` at the best point (four iterations). The final message names the best
point: row 3, column 9, GWC score 3.999, twist angle 3.164°, fractional dips at ν = 0.341, 0.572
and 0.717, the site of Figs. 3f and 3g. Nine turns, 8.5 to 8.7 minutes, $0.38 to $0.62 (the
higher figure is a first run, which writes the prompt cache).

These figures come from five runs on 14 November 2026, which were identical in every call and
argument: the simulator replays one recorded data set (`aims/examples/`, recorded on
14 March 2026) and the prompt fixes every parameter. The replayed images therefore do not
depend on where the simulated tip is.

## 5. A navigation run (15 to 60 minutes, $1 to $4)

1. Under **01 Loop**, click **Navigation**. Leave **Objective only, uncertainty as skills** ticked
   for the default prompt, or untick it for the team's prompt with the rules written in (the note
   under the prompt box says which one you have and what the console changed for the simulator).
2. **Simulator seed**: leave it empty for a random start, 500 µm from the target in a random
   direction, or enter 1 to 5 to start where the five reported runs started.
3. **Turn cap**: this mode allows 80 turns and 45 minutes per run. The five reported runs needed
   up to 127 turns and 57 minutes, and two of them would have been stopped at these limits; to
   give a run the same room, start the console with `--max-turns-cap 200 --wall-limit-min 75`
   (section 3).
4. Press **Start run**.

The agent loads the template, approaches, scans a 53 µm frame, estimates its position with the
particle filter (one to three minutes, during which nothing new appears), may reconstruct the
scan with GP regression (five to nine minutes, again silent), moves the stage, and repeats until
the predicted distance is below 150 µm. **04 Instrument** draws every estimate on the template
with its one-standard-deviation ellipse. About a third of scans at the starting tip lift are
interrupted by a tip retraction; the scan's result then says "Scan completed (failed)" and the
Z controller reads off. When the agent opens a skill, the record shows
`Skill aims-controller-recovery` or `Skill aims-gp-recovery`.

**Expected outcome.** Five runs on 14 November 2026 (Claude Opus 4.5, seeds 1 to 5, default
prompt, 200 turns and 75 minutes) all ended by the agent's own decision. Four ended 68 to 127 µm
from the target. In the fifth (seed 5) the agent never opened the skills, moved on scans that had
been interrupted, and stopped 467 µm from the target. The runs took 36 to 127 turns, 14 to 57
minutes and $0.95 to $4.17. With the team's prompt (switch unticked), the three runs on seeds 3,
9 and 10 of the navigation rule test (console of 12 November 2026) stopped 100 to 117 µm from
the target.

**Ground truth.** Each run folder holds `sim/sessions/<stamp>/truth.jsonl`, one JSON object per
event: the true tip position (`true_xy_um`, µm from the target) and distance (`true_r_um`) at the
start, after every scan and after every move, and for each scan whether the tip retracted
(`defect`) and whether the controller stayed on (`controller_on`). The agent never sees this
file; compare it with the `predict_position` results in the run record.

## 6. Instructions for use

**The page** has six numbered sections.

| Section | What it is |
|---|---|
| 01 Loop | Navigation, Measurement and Discovery as a chain; click one to load its prompt. |
| 02 Settings | Model, API key, turn cap, simulator seed, the approval switch, the objective-and-skills switch, the hand-off note, the prompt (editable), Start and Stop. |
| 03 Run record | Everything the selected run did: agent text, tool calls with arguments, results with images, your decisions, the final result. The link next to the run id downloads the run's `events.jsonl`. |
| 04 Instrument | The stage map with the position estimates, the latest image, position, distance, last move, gate and scan frame. |
| 05 Runs | The runs started from this browser, newest first; finished runs replay from their log. |
| 06 Tools on the server | The tools the MCP server reports. |

**Settings.**

- *Model*: Claude Opus 4.5 (the paper's model for loops 1 and 2), Claude Opus 4.8, Claude Opus,
  Sonnet and Haiku 5.5, GPT-5.5, or another model id.
- *API key*: for the chosen model's provider. The page sends it to the console on your computer
  with the run request; the console hands it to that run's agent process for the run's duration.
  It is not written to disk or to the console's output: with a test key, the key appeared nowhere
  in the run folder, the agent's home folder or the console's output, and `meta.json` records only
  `own_key: true`. The browser keeps it in session storage until the tab closes.
- *Hold instrument actions for approval*: approach, scans, stage moves, gate ramps and sweeps wait
  for you to press **Allow** or **Deny** (with a note the agent reads); analysis tools run on their
  own. Off, the agent runs unattended, as in the paper.
- *Prompt*: any edit you make is what the agent gets, and what the log keeps.

**During a run**: **Send** delivers a message as the agent's next turn; **Stop** ends the agent
(a tool call already on the simulator finishes there). **Between loops**: **Use as the hand-off
note** copies a finished run's final message into the hand-off field and selects the next loop.

**What is written** for each run, in `runs_local/<run id>/` (or `/runs/<run id>/` in Docker):
`events.jsonl` (every event with a time stamp), `meta.json` (loop, prompt as sent, model,
settings), `result.json` (turns, duration, cost), `sim/sessions/<stamp>/` (the simulator's scan
files and `truth.jsonl`), `work/` (the agent's working folder, with the skills when they are
offered) and `home/` (the agent program's own settings).

**Limits in this mode**: 80 turns and 45 minutes per run (raise them with the options in
section 3), four runs at a time (two in Docker), and one navigation run at a time, because of
the memory GP regression needs.

## 7. Reproducing the reported runs

| What | How |
|---|---|
| The five runs of each loop in the response to the reviewers | Start the console with `--max-turns-cap 200 --wall-limit-min 75`. Claude Opus 4.5. Navigation with **Objective only, uncertainty as skills** ticked and seeds 1 to 5, one after another; measurement with the default prompt and hand-off note. Their records (every event, the ground truth, tables) are in the data package `AIMS_loop_distributions_2026-11-14`, in the same file formats as yours. |
| The paper's navigation protocol | Untick **Objective only, uncertainty as skills**. On the simulator the stop radius is 150 µm, the termination criterion of the Fig. 2 run; the note under the prompt box shows the change. |
| The recorded runs | [leonmgarcia.com/AIMS](https://leonmgarcia.com/AIMS) replays four runs, each with its prompt and settings. |
| The discovery loop and its blinded reruns | `rerun_kit/README.md`. |

A seed fixes the start, not the run: the agent decides differently from run to run, and the
simulator's random draws (tip retractions, stage errors) follow the order of the calls, so runs
with one seed diverge as soon as their decisions do. Expect a distribution, not a copy.

## 8. What is simulated

- **Navigation.** A scan returns an image rendered from the marker template at the true tip
  position, with noise and row drift. A tip retraction interrupts a scan with probability 0.3 at
  the prompt's 500 nm tip lift, halved for every further 100 nm (at least 0.02); the scan then has
  rows of noise from some line on, its result says "(failed)", and the Z controller reads off. A
  stage move lands with a 2 % scale error and 2.5 µm of random error per axis.
- **Measurement.** Gate scans and grid spectra are replayed from the recording in
  `aims/examples/`; they do not depend on the tip position.
- **Unchanged.** The team's MCP server and its analysis tools run as released; only the hardware
  calls under them are simulated (`aims_console/sim/`).

## 9. Your key and your privacy

The console runs on your computer. The authors run no service and receive nothing from it; the
only outside party is your model provider. The page the console serves loads nothing from outside
your computer. The page at leonmgarcia.com/AIMS is a static page served by GitHub Pages, with no
analytics or tracking scripts.

## 10. When something goes wrong

| What you see | What to do |
|---|---|
| "Python 3.11 or 3.12 is needed" | Install Python 3.12 (`brew install python@3.12` on macOS) or set `PYTHON=/path/to/python3.12`. |
| An installation stopped half way | Run `reviewer.sh` again; it redoes an unfinished installation. |
| The port is in use | `PORT=8090 ./reviewer.sh`, or `docker run -p 8090:8080 ...` and open port 8090. |
| **Start run** is disabled | No key has been entered for the chosen model's provider. |
| The run ends at once with "Invalid API key" | The key is wrong or belongs to the other provider. |
| Nothing new appears for minutes | The particle filter (one to three minutes) or GP regression (five to nine minutes) is running. |
| "MCP server is not connected", or the run ends as "instrument lost" | The simulated instrument ran out of memory during GP regression. Give the console 7 GB. |
| "a navigation run is already in progress" | One navigation run at a time; start it when the other ends. |
| "runs are already in progress" | The limit on runs at a time; wait for one to end. |
| "wall-time limit reached", or the run ends at the turn cap | Raise the limits (section 3). |

## 11. Version, licence and further documentation

- Code: <https://github.com/leongarcia2023/aims-console> (branch `main`); Docker image
  `ghcr.io/leongarcia2023/aims-console`, rebuilt on every change.
- `aims/` holds the parts of the team's AIMS code the console needs (`mcpServer/`, `prompts/`,
  `templates/`) and the recorded data the simulator replays (`examples/`), under the MIT licence
  (Philip David Suh and Siyuan Qiu).
- `aims_console/MANUAL.md` is the full user guide, including use on the microscope;
  `aims_console/README.md` is the technical reference (architecture, the event log, the
  simulator).
- Questions: through the editor.
