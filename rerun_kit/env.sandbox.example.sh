# Source before run_arm.sh on this machine (the cloud container that also holds the original
# run, the revision workspace and the parent session's files). On the lab machine, build the
# HIDE_PATHS list for that machine, or use a clean container per run with SANDBOX=0.
# 2026-11-09: the console repositories and the console's own run directory added; two chained pass-2
# runs (B r02, D r01) searching for the prompt's "_v5" protocol file found identical protocol copies
# under them (nothing about the ablation; see results/chain/chained_pass2_summary.md).
export MODEL="${MODEL:-claude-opus-4-8}"   # default; MODEL=... on the command line wins (second-model campaign)
export MAX_TURNS=400
export PY_ENV_BIN=/home/claude/pr/venv/bin
export LIVE_ROOT=/tmp/mim_runs
export SANDBOX=1
export HIDE_PATHS="/home/claude/rev:/home/claude/aims:/home/claude/aims-console:/home/claude/aims-console-release:/home/claude/aims_console:/home/claude/leongarcia2023.github.io:/home/claude/aims_console.zip:/home/claude/aims_console_note.md:/home/claude/rerun_SUMMARY.md:/home/claude/rerun_summary.csv:/home/claude/pp:/home/claude/aims_revision_plan.md:/home/claude/revision_leon_items.md:/home/claude/.ssh:/home/claude/.cache:/root/.claude/projects:/root/.claude/tasks:/root/.claude/sessions:/root/.claude/shell-snapshots:/root/.claude/backups:/root/.claude/environment-manager:/root/.cache:/root/.ssh:/root/.aws:/root/.boto:/root/.config:/root/.gitconfig:/mnt/user-data:/mnt/attach:/mnt/skills"
