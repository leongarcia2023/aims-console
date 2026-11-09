# GPT reruns of the discovery loop with Codex CLI

The same first-pass rerun as the Claude runs (`run_arm.sh`), driven by OpenAI's Codex CLI signed
in with a ChatGPT account, so the runs bill the plan and need no API key. Arm C (literature held
back, no internet for the agent) is included; the other arms drop in the same way.

## One-time setup on the Mac

1. Codex CLI installed and signed in: `codex --version`, `codex login status` (or `codex login`).
2. A Python environment with the packet's dependencies, for the agent's own analysis scripts:
   ```bash
   conda create -y -n aimsrerun python=3.12 numpy scipy pandas matplotlib numba scikit-image scikit-learn
   ```
   (or `pip install` the same list into any 3.12 venv). Note its `bin` directory, e.g.
   `~/miniforge3/envs/aimsrerun/bin`.
3. Unpack this kit somewhere without spaces in the path (the AIMS folder is fine):
   `tar xzf rerun_kit_codex.tgz`.

## Run

```bash
cd rerun_kit_codex
PY_ENV_BIN=~/miniforge3/envs/aimsrerun/bin MODEL=gpt-5.5 ARMS="C_litheld" N=2 ./campaign_codex.sh
```

Two runs of arm C on GPT-5.5, one after the other, 40 to 60 minutes each if they behave like the
Claude runs. The window can be closed only after `campaign finished`; `caffeinate -i` in front
of the command keeps the Mac awake. Progress: `tail -f campaign_codex.log`. If the model name is
refused, `codex` prints the names the plan offers; set `MODEL=` to one of them and rerun (the
campaign resumes; finished runs are kept).

## What each run does

* copies the arm tree to a neutral path (`/tmp/mim_runs/<id>/packet`) and checks every file
  against the external sha256 manifest;
* gives Codex a private `CODEX_HOME` holding only a copy of the sign-in, a config that fixes the
  model, approvals off, `workspace-write` sandbox with network off for the agent's commands, and
  the discovery skill; the operator's own Codex settings, history and AGENTS.md are not visible;
* sends the first-pass prompt exactly as the Claude runs received it, followed by the full text
  of `mim_discovery_skill` (Codex has no Claude skills; the skill is also installed under
  `CODEX_HOME/skills`);
* records the JSON event stream (`events.jsonl`), the final message, `meta.txt`, the canary;
* moves the tree to `runs/C_litheld/run_NN/work`, deletes the private home (and with it the
  sign-in copy), and writes `audit.txt` (network commands, pip installs, paths outside the packet,
  sweeps of `/` or `~`, mentions of the ablation) and `transcript.json` (final message, turns,
  token usage; cost null, since the plan bills nothing per run).

## What is different from the Claude runs, to be stated with the result

* The sandbox is Codex's (macOS Seatbelt). Writes are confined to the packet and the agent's
  commands have no network, but the rest of the disk is readable. The Claude runs hid the host
  filesystem; here the audit's "paths outside the packet" and "sweeps" lines are the isolation
  evidence. Keep the as-run results and the other arms off this Mac, or at least out of any path
  an agent would guess, while the runs go.
* The skill arrives in the prompt rather than as a tool the agent invokes.
* Token counts come from Codex's events; the accounting (cached vs uncached) is OpenAI's.

## Afterwards

`python3 aggregate_runs.py --runs runs --out results` builds the same per-run table as for the
Claude runs (favoured hypothesis, closed posterior, P_mech, U_closure, melting temperatures,
turns, minutes). To hand the runs back to the authors: `zip -r codex_runs.zip runs`.
