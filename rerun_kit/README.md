# Discovery-loop rerun kit

The scripts that produced the blinded reruns of the AIMS discovery loop reported in the paper's
supplementary material (the contamination ablations). They run the loop's first pass or second pass
with the headless `claude` CLI inside a self-contained copy of the discovery packet, with the
internet, the answer-bearing literature, two sentences of the physics prior, or the device-specific
voltage windows removed, and they keep the evidence that each run was blind. Nothing in the AIMS
repository is modified; the packet is copied into an arm tree and the agent works in a copy of that.

The console in this repository does not run the discovery loop (it needs file and shell tools and
the packet); this kit does. It is Linux-only as written (`sandbox_exec.sh` uses mount namespaces).

## What you need

* The discovery packet (`discoveryLoopPacket/`), the prompts (`Physics_prior.md`, the protocol,
  `Discovery loop prompt.md`), the released analysis tools and the `mim-discovery-skill` directory,
  all from the AIMS repository.
* The `claude` CLI (Claude Code) and either `ANTHROPIC_API_KEY` or the CLI's credentials file.
* A Python environment with the packet's dependencies (numpy, scipy, pandas, matplotlib, numba,
  scikit-image, scikit-learn); its `bin` directory goes in `PY_ENV_BIN`.
* For arms B to E, an egress allowlist on the machine that admits only the model API (the runner
  removes the web tools and records a canary, but it does not enforce egress itself).
* For the second pass in the published design, the paper's first-pass `analysis_results/` and the
  released ED/FTLM cache (`SupplementaryData/ED_FTLM_epsweep_N18-20-18_epsperp5.987.json`); for the
  chained design, a cache at eps_perp 4.5 computed once with the team's `ED_FTLM_epsweep.py`.

## Arms

| Arm | Internet | Literature | Physics_prior.md | Melting tool | Question |
|---|---|---|---|---|---|
| A_asrun | on | all | as released | as released | run-to-run spread of the released setup |
| B_nointernet | off | all | as released | as released | does the conclusion need the internet |
| C_litheld | off | answer-bearing papers removed | as released | as released | derived from the engines, or retrieved |
| D_litheld_noprior | off | as C | two filling-assignment sentences removed | as released | derived without the prior's assignment |
| E_nowindows | off | as C | as D | device-specific gate windows removed | voltage-to-filling map derived from the data |

Withheld in C, D and E: Zong et al. 2025 (quantum melting in twisted bilayer MoSe2) and its
Supplementary Information, and Kumar et al. 2026 (quantum-fluctuation shifts of GWC melting
temperatures). `--withhold` overrides the list; `--withhold-borderline` also removes Kumar,
Lewandowski and Changlani 2025.

Two first-pass variants exist for every arm. The plain arm holds the two-section loop prompt and the
packet's `SupplementaryData/` (the second-pass material: AFM data, registration code, the ED/FTLM
script). The `_p1only` arm, built with `--first-pass-only --suffix _p1only`, truncates the prompt
before "## Second pass" and drops `SupplementaryData/`, so the agent sees neither the second-pass
paragraph nor the second-pass data. The plain arms' mechanism labels and melting temperatures are unaffected by that
material; their follow-up plans are, which is why the `_p1only` arms exist.

## Commands

```bash
# 1. arms (manifests go to arms/manifests/, outside the trees; the build refuses a tree that
#    mentions the ablation)
python3 build_arms.py --packet AIMS/discoveryLoopPacket --prompts AIMS/prompts \
    --tools AIMS/mcpServer --skill /path/to/mim-discovery-skill --out arms   # the skill unpacked from AIMS/skills/mim-discovery-skill.skill
python3 build_arms.py ... --out arms --first-pass-only --suffix _p1only --arms B_nointernet C_litheld D_litheld_noprior E_nowindows

# 2. machine settings: copy env.sandbox.example.sh to env.sandbox.sh and set HIDE_PATHS to every
#    directory a run must not see (the paper, the released results, this kit's other runs, your
#    own session files); campaign.sh sources it
PY_ENV_BIN=/path/to/venv/bin MODEL=claude-opus-4-8 ARMS="C_litheld_p1only D_litheld_noprior_p1only" N=3 ./campaign.sh

# 3. second pass, published design (the paper's first pass as input, released cache)
python3 build_pass2_arms.py --asrun /path/to/as-run-packet --bases B_nointernet C_litheld D_litheld_noprior
#    second pass, chained design (a rerun's own first pass as input; the script then falls back to
#    eps_perp 4.5, so the builder wants that cache and refuses any other)
python3 build_pass2_arms.py --asrun /path/to/as-run-packet --bases C_litheld \
    --pass1 C_litheld_p1only/run_01 --suffix p2chain_r01 --cache /path/to/ED_FTLM_epsweep_N18-20-18_epsperp4.5.json
PASS=2 ARMS="C_p2chain_r01" N=1 ./campaign.sh

# 4. tables
python3 aggregate_runs.py --runs runs --out results          # first pass
python3 aggregate_pass2.py --runs runs --out results          # second pass (add --ref-cache for the 4.5 arms)
python3 token_ledger.py runs/C_litheld_p1only/run_01          # per-call token and cost ledger of one run
python3 audit_run.py runs/C_litheld_p1only/run_01             # tools, pip, host sweeps, outside paths, mentions
python3 fig_reruns.py --summary results/rerun_summary.csv --out results/rerun_distribution
```

`campaign.sh` runs one arm at a time with retries: a usage or rate limit removes the run and sleeps,
an empty transcript is retried a few times, an environment fault (canary reachable, integrity
mismatch, probe or hiding check failed) stops the campaign. `BUDGET_MIN` makes it exit cleanly
before a wall-clock budget so a relaunch resumes. After each kept run the exact copies of `data/`
and `literature/` inside `work/` are pruned when every file still matches the manifest.

## Blinding, and what each run keeps as evidence

Four ways the rerun agent could learn what the arm tested were found in a pilot and are closed in
`run_arm.sh`: an in-tree manifest naming the withheld papers (manifests now live outside the tree
and the build refuses a tree that mentions the ablation); the arm name in the working path (a run
works at a neutral path, `/tmp/mim_live/packet`, and is moved into `runs/ARM/run_NN/work` after
the agent exits); the nested CLI inheriting the parent session's environment (the CLI starts under
`env -i` with a private HOME holding only credentials and the skill, and a one-turn probe refuses
the run if the CLI reports an attached project, a memory snapshot or a tool outside the file and
shell set); and `find /` locating the released results on the same machine (`sandbox_exec.sh`
mounts empty directories over everything in `HIDE_PATHS`, drops capabilities so the mounts cannot
be undone, and the runner verifies from inside that every hidden path is empty).

Each `runs/ARM/run_NN/` holds `integrity.txt` (sha256 of the copy against the manifest),
`canary.txt` (can `https://example.com` be reached), `hidden.txt`, `probe.txt`, `meta.txt` (the
runner's clock), the CLI's `transcript.json`, the agent's complete session log `agent_log.jsonl`
(every message, tool call and result), `audit.txt`/`audit.json`, and after `token_ledger.py`
`tokens_per_call.csv`, `tokens_summary.json`, `agent_system_prompt.txt` and `tools_available.json`.
Cost is the CLI's own `cost-state` total; `tokens_per_call.csv` dedupes the log by message id
(summing the per-block usage records counts each message about 2.4 times).

Two bookkeeping facts worth knowing before reading the tables: the CLI's `num_turns` and
`duration_ms` restart when a background tool's completion re-enters the agent, so
`aggregate_runs.py` also records the number of API requests and the runner's wall time; and the
container's egress allowlist admitted PyPI in the authors' runs, so `audit.txt` lists every pip
call (none carried physics).

## Second-model runs

`MODEL=` selects the Claude model (`claude-opus-5-5`, `claude-opus-4-5` were used for the checks on
arm C). `codex_kit/` runs the same first pass with OpenAI's Codex CLI on a ChatGPT plan for the
GPT-5.5 check, with its own audit script; see its README.

## What has been run (as of 2026-11-08)

First pass, Claude Opus 4.8: 20 runs on the plain arms (B, C, D, E, five each) and 12 on the
`_p1only` arms (three each). Second-model checks on arm C: two runs each on Claude Opus 5.5 and
Claude Opus 4.5. Second pass, published design: nine runs on Opus 4.8 (B, C, D, three each) and two
on Opus 4.5 (C). Second pass, chained design (each `_p1only` B, C, D run continued into its own
second pass with the eps_perp 4.5 cache): nine arms, campaign in progress. The results are in the
paper's supplementary material.
