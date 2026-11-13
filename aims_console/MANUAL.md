# AIMS console: user guide

This is the hands-on guide for someone new to the console. `README.md` covers the architecture,
the event contract and the simulator; this file covers what to click, in what order, and what
to expect. Read section 1, then either section 3 (the public page) or sections 2 and 4 (your own
installation) once in simulation, then the rest as needed.

## 1. What you are looking at

The console is one page that runs the three AIMS loops and keeps the record of every run. It
is laid out in six numbered sections.

| Section | What it is |
| --- | --- |
| 01 Loop | The three loops as a chain: Navigation, Measurement, Discovery, with a hand-off arrow between them. Click a step to select it; its prompt loads into the Prompt box. |
| 02 Settings | Model (the study's models and a free field), API keys when the service asks for them, turn cap, the approval switch, the recovery-skills switch, the hand-off note, the prompt, Start and Stop. |
| 03 Run record | The ledger of the selected run: agent text, each tool call with its arguments, each result with its images, operator decisions, the final result. New events append at the bottom. |
| 04 Instrument | What the instrument is doing, read from the tool results: the stage map, six tiles, the latest image, the final message. |
| 05 Runs | Every run on record, newest first. Click one to open its ledger; finished runs replay from their log. |
| 06 Tools on the server | The tool list the MCP server reports. If it says "server unreachable", nothing else will work (see section 10). |

The tag at the top right says **SIMULATION** or **HARDWARE**. In simulation the server runs on
a virtual stage and replayed device data; nothing moves. In hardware mode every approved stage
move happens on the microscope.

A copy of the page exists at `leonmgarcia.com/AIMS` (password from León). When its console
service is up, it runs the navigation and measurement loops on a simulated instrument with your
own API key (section 3). When the service is down, it replays four recorded simulator runs at
about fifteen times speed, and Start, Stop, Send and Allow/Deny do nothing.

## 2. Setting up

Skip this section if you only want to run the console on your own machine; section 3 is two commands.

You need Python 3.11 or 3.12, a clone of the AIMS repository, and either the `claude` command line
signed in (`npm install -g @anthropic-ai/claude-code`, then `claude login`) or an
`ANTHROPIC_API_KEY` in the environment. Then:

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r aims_console/requirements.txt     # the console and the team's server requirements, without the hardware libraries
```

Two processes run: the MCP server (real or simulated) and the console.

```bash
# A. the simulator: the team's server on a virtual instrument. Nothing in the repository is modified.
python aims_console/sim/start_server_sim.py --mcp-dir /path/to/AIMS/mcpServer --port 8000 --seed 0 --p-defect 0.3

# A'. or the real instrument, as usual
cd /path/to/AIMS/mcpServer && python start_server_sse.py 8000

# B. the console
./aims_console/run_console.sh --repo /path/to/AIMS --mcp-url http://localhost:8000/sse --sim \
    --model claude-opus-4-5 --packet /path/to/discoveryLoopPacket --port 8080
```

Drop `--sim` when the server is the real one. Open `http://127.0.0.1:8080`. Check three
things: the mode tag is the one you expect, the byline names the right MCP server, and section
06 lists about forty tools.

The console binds to 127.0.0.1 only. To reach it from another machine, use an SSH tunnel
(`ssh -L 8080:127.0.0.1:8080 labmachine`); do not put it on an open port, because approving a
stage move is an instrument action.

## 3. Run it on your own machine (reviewers start here)

The console runs on your computer against a simulated instrument, on your own API key. The
key goes into the page and stays on your machine; the authors run no service and see nothing.

1. Start it. With Docker: `docker run --rm -p 8080:8080 ghcr.io/leongarcia2023/aims-console`.
   Without Docker (git and Python 3.11 or 3.12):
   `curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/install.sh | bash`,
   which clones the repository into `./aims-console-local`, creates a virtualenv and starts the
   console (a few minutes the first time).
2. Open `http://localhost:8080`. The note under the title says the console is public mode: each
   run gets its own simulated instrument and runs on the key you enter.
3. Pick a model under 02 Settings. The list is the study's models: Claude Opus 4.5 (the paper's
   navigation and measurement loops), Claude Opus 4.8 (the discovery loop and the contamination
   reruns), GPT-5.5 (the paper's benchmark), and Sonnet and Haiku 5.5 for a cheap first try.
   "Other" takes any model id the provider accepts.
4. Paste an API key for that model's provider (Anthropic or OpenAI). It travels only to the
   local service and into that run's process; nothing writes it to disk. The browser keeps it in
   session storage until the tab closes.
5. Leave **Recovery steps as skills** ticked to see the two recovery steps of the navigation
   prompt run as skills (section 4), or untick it for the prompt exactly as in the paper.
6. Press **Start run** and read section 4 for what you will see. Caps: 80 turns and 45 minutes
   per run, two runs at a time with the Docker image. The discovery loop needs file and shell
   tools and the discovery packet; the local console does not offer it (the rerun kit does).

The page at `leonmgarcia.com/AIMS` (password from León) replays four recorded runs and carries
the same two commands; its service field is preset to `http://127.0.0.1:8080`, so it can also
drive a console running on your machine if your browser allows a page served over HTTPS to
talk to localhost (Chrome and Firefox do; if yours refuses, `http://localhost:8080` serves the
same page from the local service).

The cost of a run is yours and appears with the final result (the Claude driver reports it; the
GPT driver reports token counts). The console's own short test run, nine turns on Haiku, cost
under a cent; a full navigation run on an Opus model costs more, mostly for the scan images the
agent looks at. The particle filter runs on your machine and costs nothing in tokens, but takes
one to two minutes per estimate.

## 4. First run: navigation in simulation

1. Click **Navigation** in the chain. The prompt box fills with the team's
   `prompts/Navigation loop prompt.md`. The console adds one line at the top, step 0, which
   loads the template; the particle filter cannot run without it. You can edit any of the text
   before starting; the edited text is what the agent gets and what the log keeps.
2. Leave the model as it is for a first try (a small model is fine for the mechanics; the
   paper's runs used Claude Opus 4.5 here). Turn cap 200 is plenty on a lab machine; the public
   service caps it at 80.
   **Recovery steps as skills**: steps 3 and 7 of the team's prompt (controller off: retry the
   approach with 100 nm more lift; uncertainty above 53 µm: GP-reconstruct the scan and predict
   again) are conditional recoveries. Ticked, the prompt delegates them to two skills,
   `aims-controller-recovery` and `aims-gp-recovery`, which the agent invokes when the condition
   arises; the ledger shows `Skill aims-gp-recovery` and then the calls it makes. Unticked, the
   prompt spells the steps out, as in the paper. Same protocol either way.
3. Tick **Hold instrument actions for approval**. With it on, these calls pause the agent until
   you decide: `approach_and_lift`, `retract_tip`, `scan`, `move_tip`, `execute_movement`,
   `set_scan_frame`, `nanonis_xy_set`, the gate ramps and the sweepers. Analysis tools run on
   their own. With it off the agent runs the whole protocol unattended, as in the paper.
4. Press **Start run**. The run appears in 05 Runs and its ledger opens in 03.

What you will see, in order:

- A `session` row (how many tools the agent has, the MCP server status, the model).
- `call load_template`, then its result. The stage map in 04 keeps the geometry from this call.
- A rust callout, **Approval needed**, for `approach_and_lift`. Read the arguments, type a note
  if you want (the agent sees it), press **Allow**. The decision and the note go into the
  ledger as an operator row.
- `scan`, then `get_sxm_image`: the scan appears in the ledger and under Latest image. Scans
  cover a square frame (53 µm in the navigation loop) on a 128 × 40 pixel grid, so the page
  shows them square, pixel for pixel; the caption gives the grid and the frame.
- `predict_position`. The particle filter takes about two minutes per estimate on a laptop;
  the ledger shows nothing while it runs. When it returns, the Position and Distance tiles
  fill and the estimate is drawn on the stage map: a steel dot on the template, the one-sigma
  ellipse around it, the scan frame as a dashed square, the target as a rust diamond at the
  origin. **Template** shows the whole template (1433 × 953 µm); **Close-up** zooms to the
  target. Earlier estimates stay as small dots joined by a line, so you can see the approach.
- `calculate_movement_from_prediction`, then an approval for `execute_movement`. The Last move
  tile shows the stage steps.
- The loop repeats until the distance is under 50 µm or the agent stops. The final message
  appears under Final message with the turn count and cost, and the run's tag turns to
  **finished**.

Position is the scan centre relative to the target, in µm, x to the right and y up, the
particle filter's own convention. Uncertainty is one standard deviation. Confidence is the
filter's own number between 0 and 1; low values are normal on the first scan.

In simulation about a third of scans (`--p-defect 0.3`) carry a tip-retraction defect, rows of
noise; the prompt's step 7 (or the aims-gp-recovery skill) then sends the image through GP regression before predicting
again. That is the protocol working, not a fault. The simulator writes the true position after
every scan and move to `sessions/sim/<stamp>/truth.jsonl` in the server directory; the agent
never sees that file, so it is the yardstick for how well the loop did.

GP regression is the slow step. One call takes 5 to 9 minutes on two cores and its memory peaks
at about 5.4 GB, and the ledger shows nothing meanwhile. The console gives a tool call
45 minutes (`AIMS_TOOL_TIMEOUT_MIN`), and the simulator runs its tools in worker threads so that
the connection stays alive during a long call.

What to expect at the target (found 2026-11-12). The navigation prompt released with the code
stops when the predicted distance is under 50 µm. On the marker chip the centre is empty: the
nearest marker is 42 µm from the target, and a 53 µm scan frame centred within 30 µm of it
shows no marker in two directions out of three. The simulator draws its scans from the
template, so there the particle filter returns a wrong position with a large uncertainty, GP
regression does not repair it, and the loop leaves the target and comes back. A scripted
operator that follows the prompt without GP regression (no model, seeds 3, 9 and 10, 16 scan
cycles each) did not stop in any of its three runs, although the stage had been within 50 µm of
the target eight times. A run that reaches a cap this way shows this limit, not a fault of the
agent; `truth.jsonl` shows where the stage was. The radius is in steps 9 and 10 of the prompt,
which the page lets you edit. The paper's Methods give 150 µm for the published navigation run
(the team confirmed it on 2026-11-12, and that a simulator test on the marker chip should use
150 µm), and with 150 µm the same three scripted runs end after 5 to 8 cycles, 77 to 110 µm from
the target.

When a run ends (changed 2026-11-12). The console's operating rules, the short system prompt
every run gets, tell the agent to stop when the protocol's end condition is met. Until
2026-11-12 they also said "or a tool reports experiment_complete". `predict_position` reports
that flag whenever the predicted distance is under 50 µm, whatever the uncertainty, so a run
could end on a prediction that step 7 sends to GP regression first. In the rule test with the
paper's model all five runs with the recovery steps that the agent ended itself ended that way,
90 to 282 µm from the target. The clause is gone; the flag is still in the tool's answer.

Builds of the simulator from before 2026-11-12 also moved the stage too far: 1.84 times the
intended distance in y and 1.07 times in x. The simulated stage took its step sizes from the
repository's `config/stage_config.yaml`, which the server does not find when it runs in
`mcpServer/`, while the controller computed with its built-in ones. Navigation runs recorded
with those builds show it in every stage move, the nine runs of the rule test with the paper's
model among them (there the loop stopped by itself in 2 of 6 runs that had the recovery steps).
Now the stage moves by the controller's own calibration; `--stage-step-um=X,Y` on
`start_server_sim.py` sets another.

## 5. The hand-off, and the measurement loop

When a run finishes, press **Use as the hand-off note**. The final message is copied into the
hand-off field and the next step of the chain is selected. Edit the note: keep what the next
loop needs (where the device is, what was found), remove the rest. The note is prepended to
the next prompt and kept in the log with the decision. This is the manual hand-off the paper
describes between its automated loops; the console makes it a visible, logged step.

The measurement prompt has blanks to fill before starting: the gate range `V1`, `V2` and
`V_steps`, the `folder` name, the region size `a` × `b` and the grid `n` × `m`. Fill them in
the prompt box. In simulation, gate scans and the grid sweep replay recorded device data
(`examples/VbgScans`, `examples/GridSpectra`), so the images are real but do not depend on
where the virtual tip is. The Bottom gate and Scan frame tiles follow the ramps and frames.

## 6. The discovery loop

Discovery does not touch the instrument. The console copies the discovery packet (`--packet`)
into `runs_console/<run id>/packet/` and gives the agent file and shell tools there; the
approval switch does not apply. The first pass of the protocol is what runs. Expect a long run
(the contamination reruns averaged about forty minutes for the first pass); set the turn cap to
400 and the model deliberately. Results land in the run's `packet/analysis_results/`.

## 7. Talking to the agent during a run

- **Send**: the message box delivers text as the agent's next turn. Use it to redirect
  ("skip the GP regression step", "stop after the next scan").
- **Deny** with a note: the agent gets the refusal and the note, and decides what to do next.
- **Stop**: interrupts the agent and denies anything waiting for approval. A tool call that has
  already reached the server finishes there; the console cannot abort a scan or a ramp in
  progress. For that, use Nanonis itself.

## 8. Where everything is written

Each run has a folder under `runs_console/<run id>/`:

- `events.jsonl`: every event with a timestamp (the ledger is a view of this file; the link
  next to the run id downloads it),
- `meta.json`: loop, prompt as sent, model, settings, hand-off note,
- `result.json`: the final result, turns, duration, cost,
- `packet/` for discovery runs.

The MCP server writes its own session folders (`sessions/`), including the `.sxm` files and,
in simulation, `truth.jsonl`.

## 9. On the microscope

Before the first hardware run:

- Start the team's real server and the console without `--sim`; the tag must read HARDWARE.
- Keep approvals on for the first runs, with someone at the instrument who can see the stage
  and Nanonis.
- Read every approval's arguments before allowing: the approach limits (`low_limit`,
  `high_limit`, `tip_lift`) and the step counts of a move are the two that matter.
- Use the paper's model for the navigation and measurement loops.
- Stop in the console ends the agent, not a motion already commanded.

## 10. When something looks wrong

| Symptom | Cause and fix |
| --- | --- |
| 06 says "server unreachable" | The MCP server is not running or `--mcp-url` is wrong. Start it, then reload the page. |
| `predict_position` returns "Template not loaded" | Step 0 was removed from the prompt, or the server was restarted after it ran. Put the step back, or send "load the template again" through the message box. |
| No approval callout appears | The switch is off, or the loop is Discovery (no instrument tools there). |
| The agent stalls after a tool call | Look at the server's terminal: the particle filter takes about two minutes, a real scan takes as long as the scan. |
| A run is tagged **interrupted** | The console was restarted while the run was live. The ledger holds everything logged up to that point. |
| Starting a run fails at once | The `claude` command line is not signed in or `ANTHROPIC_API_KEY` is not set in the shell that started `run_console.sh`. |
| The page's type looks like Georgia | The bundled Latin Modern fonts did not load (served from `web/fonts/`); the page still works. |
| The public page does not open from the SLAC network | The network resets connections to that domain. Use a hotspot, or the artifact link León can share. |
| Start run is disabled | No key for the chosen model's provider has been entered, or the page is in replay mode (the note under the title says). |
| "needs its access token" | The page and the service were built with different tokens. Rebuild the page with the service's token (section 11). |
| "runs are already in progress" | The concurrency cap (two with the Docker image, four by default). Wait a few minutes; runs end at 45 minutes at the latest. |
| The run errors at once with an authentication message | The key is wrong or for the other provider. The error text is the provider's own. |
| "wall-time limit reached" in the ledger | Public mode stops a run at 45 minutes. The log up to that point is complete. |
| The agent waits on `run_gp_regression` and the call never returns | A build of the simulator from before 2026-11-12 computed on the server's event loop, and the client dropped the silent connection after about five minutes. Update: the tools now run in worker threads. The microscope's own server still computes on its event loop, so the same can happen there with a tool that runs longer than that. |
| "MCP server is not connected" in the middle of GP regression | The machine ran out of memory and the simulator was killed. One GP regression peaks at about 5.4 GB (concurrent runs take turns at it). Give Docker, or the machine, 7 GB, and about 1 GB more for every further run at the same time. |
| The navigation loop reaches the target and moves away again | The stop radius of the prompt against the empty centre of the marker chip, described at the end of section 4. |
| In a recorded run the stage moves further than the 100 µm the controller allows | A build of the simulator from before 2026-11-12: its stage moved 1.84 times too far in y (end of section 4). Update. |
| In a recorded run the agent stops on a prediction with an uncertainty above 53 µm, without GP regression | A build of the console from before 2026-11-12: its operating rules said to stop when a tool reports `experiment_complete` ("When a run ends", section 4). Update. |

## 11. Rebuilding the page at leonmgarcia.com/AIMS

```bash
python aims_console/static/build_static.py --repo /path/to/AIMS --runs aims_console/runs_console \
    --out /path/to/site/AIMS --password <password> --include <run ids> \
    --runlocal-repo https://github.com/leongarcia2023/aims-console --runlocal-image ghcr.io/leongarcia2023/aims-console
```

It inlines the page, the recorded runs and the template into one document, encrypts it under
the password (PBKDF2-SHA256, 600,000 iterations, AES-GCM), and writes the gate page beside it.
`--runlocal-repo <url>` (and `--runlocal-image <image>`) puts the run-it-yourself commands on
the page. `--backend` and `--token` are for a hosted service, if one is ever run (`deploy/`);
both ride inside the encrypted body. Without `--password` it generates one and prints it once.
`--include <run ids>` picks the recorded runs. Add `--artifact` for a copy meant for a claude.ai
artifact (fonts and script inlined, ciphertext as text).
