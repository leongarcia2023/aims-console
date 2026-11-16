# Copy to env.sandbox.sh and edit for your machine; campaign.sh sources it before run_arm.sh.
# HIDE_PATHS must list every directory a run must not see: the paper, the released (as-run) results,
# this kit's other runs and arms, and your own session and credential folders. Each is replaced by an
# empty, read-only mount inside the sandbox, and the runner checks from inside that each is empty.
# On a clean container per run, set SANDBOX=0 instead.
export MODEL="${MODEL:-claude-opus-4-8}"   # default; MODEL=... on the command line wins
export MAX_TURNS=400
export PY_ENV_BIN=/path/to/venv/bin        # a Python environment with the packet's dependencies
export LIVE_ROOT=/tmp/mim_runs
export SANDBOX=1
export HIDE_PATHS="/path/to/paper:/path/to/as-run-results:/path/to/other-runs:$HOME/.ssh:$HOME/.cache"
