# Source before run_arm.sh on this machine (the cloud container that also holds the original
# run, the revision workspace and the parent session's files). On the lab machine, build the
# HIDE_PATHS list for that machine, or use a clean container per run with SANDBOX=0.
export MODEL="${MODEL:-claude-opus-4-8}"   # default; MODEL=... on the command line wins (second-model campaign)
export MAX_TURNS=400
export PY_ENV_BIN=/home/claude/pr/venv/bin
export LIVE_ROOT=/tmp/mim_runs
export SANDBOX=1
export HIDE_PATHS="/home/claude/rev:/home/claude/aims:/home/claude/pp:/home/claude/aims_revision_plan.md:/home/claude/revision_leon_items.md:/home/claude/.ssh:/home/claude/.cache:/root/.claude/projects:/root/.claude/tasks:/root/.claude/sessions:/root/.claude/shell-snapshots:/root/.claude/backups:/root/.claude/environment-manager:/root/.cache:/root/.ssh:/root/.aws:/root/.boto:/root/.config:/root/.gitconfig:/mnt/user-data:/mnt/attach:/mnt/skills"
