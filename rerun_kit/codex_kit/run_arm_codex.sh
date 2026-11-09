#!/usr/bin/env bash
# run_arm_codex.sh ARM N
#   The discovery-loop rerun (first pass) with OpenAI's Codex CLI instead of the claude CLI, on a
#   Mac where `codex` is installed and signed in (ChatGPT account or API key). One private
#   environment per run, same arm trees and manifests as the Claude reruns, same outputs
#   (runs/ARM/run_NN/{meta.txt,events.jsonl,final_message.md,transcript.json,work/,audit.txt}).
#
#   MODEL=gpt-5.5 ./run_arm_codex.sh C_litheld 2
#
# What is the same as run_arm.sh: the arm tree is copied to a neutral path, checked against its
# external sha256 manifest, the agent runs with the first-pass prompt, web tools are off, the
# agent's shell commands have no network, the tree is moved into runs/ after the agent exits, and
# the log is audited for pip/curl/git calls, paths outside the packet and mentions of the ablation.
#
# What differs, and must be reported with the result:
#   * the skill: Codex has no Claude skills, so mim_discovery_skill is installed under
#     $CODEX_HOME/skills and ALSO reproduced in the prompt, after the first-pass instructions;
#   * the sandbox: Codex's own (macOS Seatbelt; writes confined to the packet, network off for
#     the agent's commands); the rest of this Mac's disk is readable, so the audit's
#     "paths outside the packet" line is the isolation evidence, not a hidden filesystem;
#   * tokens and cost: read from Codex's JSON events (input, cached input, output tokens); no
#     dollar figure, since the ChatGPT plan bills none per run.
set -euo pipefail
ARM="${1:?arm name, e.g. C_litheld}"
N="${2:-2}"
MODEL="${MODEL:-gpt-5.5}"
LIVE_ROOT="${LIVE_ROOT:-/tmp/mim_runs}"
REASONING="${REASONING:-high}"           # Codex model_reasoning_effort
ROOT="$(cd "$(dirname "$0")" && pwd)"
ARMDIR="$ROOT/arms/$ARM"
MANIFEST="$ROOT/arms/manifests/$ARM.json"
RUNS="$ROOT/runs/$ARM"
REAL_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

command -v codex >/dev/null || { echo "codex CLI not found on PATH (npm i -g @openai/codex, then codex login)"; exit 1; }
[ -d "$ARMDIR" ] || { echo "no such arm: $ARMDIR"; exit 1; }
[ -f "$MANIFEST" ] || { echo "no manifest: $MANIFEST"; exit 1; }
[ -e "$ARMDIR/manifest.json" ] && { echo "in-tree manifest.json found in $ARMDIR; rebuild the arms"; exit 1; }
case "$LIVE_ROOT" in *ablation*|*litheld*|*noprior*|*nowindows*|*asrun*|*nointernet*) echo "LIVE_ROOT must be a neutral path: $LIVE_ROOT"; exit 1;; esac
[ -f "$REAL_CODEX_HOME/auth.json" ] || [ -n "${OPENAI_API_KEY:-}" ] || { echo "not signed in: no $REAL_CODEX_HOME/auth.json and no OPENAI_API_KEY (run: codex login)"; exit 1; }
JSON_FLAG=""; codex exec --help 2>/dev/null | grep -q -- '--json' && JSON_FLAG="--json"
LASTMSG_FLAG=""; codex exec --help 2>/dev/null | grep -q -- '--output-last-message' && LASTMSG_FLAG=1
echo "codex $(codex --version 2>/dev/null | head -1); model=$MODEL; json events: ${JSON_FLAG:-no}"

# the first-pass prompt, exactly as run_arm.sh extracts it, then the skill text
PROMPT_HEAD="$(sed -n '/## First pass/,/## Second pass/p' "$ARMDIR/Discovery loop prompt.md" | sed '1d;$d')"
SKILL_TEXT="$(cat "$ARMDIR/mim-discovery-skill/SKILL.md")"
PROMPT="$PROMPT_HEAD

---
The skill named mim_discovery_skill is reproduced below in full. Follow it exactly as the instructions above require; it is also installed as a skill in this environment.

$SKILL_TEXT"

mkdir -p "$RUNS" "$LIVE_ROOT"
existing=$(find "$RUNS" -maxdepth 1 -type d -name 'run_*' | wc -l | tr -d ' ')
for i in $(seq 1 "$N"); do
  idx=$((existing + i))
  RUN="$RUNS/run_$(printf '%02d' "$idx")"
  ID="$(date +%s)_$RANDOM"
  LIVE="$LIVE_ROOT/$ID"
  mkdir -p "$RUN" "$LIVE"
  echo "== $ARM run $idx ($RUN)"

  # 1. a fresh copy of the arm tree, checked against the external manifest
  cp -R "$ARMDIR/." "$LIVE/packet/"            # the whole arm tree, skill directory included, as run_arm.sh does
  python3 - "$MANIFEST" "$LIVE/packet" > "$RUN/integrity.txt" <<'PY' || { cat "$RUN/integrity.txt"; rm -rf "$LIVE"; exit 3; }
import hashlib, json, sys
from pathlib import Path
man = json.load(open(sys.argv[1])); root = Path(sys.argv[2])
files = sorted(p for p in root.rglob("*") if p.is_file())
rel = {str(p.relative_to(root)): p for p in files}
bad = [k for k, p in rel.items() if man["sha256"].get(k) != hashlib.sha256(p.read_bytes()).hexdigest()]
missing = sorted(set(man["sha256"]) - set(rel))
if bad or missing:
    print("INTEGRITY FAILED", "mismatched:", bad[:5], "missing:", missing[:5]); sys.exit(1)
print(f"integrity ok: {len(rel)} files match {Path(sys.argv[1]).name}")
PY

  # 2. a private CODEX_HOME: the sign-in alone, the run's config, the skill. Nothing else of
  #    the operator's Codex setup (AGENTS.md, history, other skills, MCP servers) is visible.
  CH="$LIVE/codex_home"
  mkdir -p "$CH/skills"
  AUTH_NOTE="chatgpt-account"
  if [ -n "${OPENAI_API_KEY:-}" ]; then
    AUTH_NOTE="api-key"            # the plan's Codex offers only the codex-tuned models; plain gpt-5.5 needs the API
  elif [ -f "$REAL_CODEX_HOME/auth.json" ]; then
    cp "$REAL_CODEX_HOME/auth.json" "$CH/auth.json" && chmod 600 "$CH/auth.json"
  fi
  cp -R "$ARMDIR/mim-discovery-skill" "$CH/skills/mim_discovery_skill"
  MODEL_LINE="model = \"$MODEL\""
  [ "$MODEL" = default ] && MODEL_LINE="# model: the plan's default (MODEL=default); the name Codex reports is appended to meta.txt"
  cat > "$CH/config.toml" <<TOML
$MODEL_LINE
model_reasoning_effort = "$REASONING"
approval_policy = "never"
sandbox_mode = "workspace-write"
[sandbox_workspace_write]
network_access = false
TOML

  # 3. canary and meta
  canary="internet reachable from this Mac (the agent's own commands are sandboxed without network)"
  curl -s -m 5 -o /dev/null https://example.com || canary="no internet from this Mac"
  echo "canary: $canary" > "$RUN/canary.txt"
  echo "model=$MODEL arm=$ARM driver=codex auth=$AUTH_NOTE reasoning=$REASONING web_tools=off sandbox=codex-workspace-write-no-network live_id=$ID packet=$LIVE_ROOT/<id>/packet start=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)" > "$RUN/meta.txt"

  # 4. the run
  LAST=""
  [ -n "$LASTMSG_FLAG" ] && LAST="--output-last-message=$RUN/final_message.md"
  ( cd "$LIVE/packet" && env -i HOME="$HOME" PATH="${PY_ENV_BIN:+$PY_ENV_BIN:}$PATH" TERM=dumb LANG="${LANG:-en_US.UTF-8}" \
      CODEX_HOME="$CH" MPLBACKEND=Agg ${OPENAI_API_KEY:+OPENAI_API_KEY="$OPENAI_API_KEY"} \
      codex exec $JSON_FLAG --skip-git-repo-check -C "$LIVE/packet" $LAST "$PROMPT" \
      > "$RUN/events.jsonl" 2> "$RUN/stderr.log" ) || echo "run $idx exited nonzero" >> "$RUN/meta.txt"
  echo "end=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)" >> "$RUN/meta.txt"
  # the model Codex actually used, as its events report it (the plan's default when MODEL=default)
  reported=$( (grep -o '"model": *"[^"]*"' "$RUN/codex_session.jsonl" "$RUN/events.jsonl" 2>/dev/null || true) | head -1 | sed 's/.*"model": *"\([^"]*\)"/\1/' || true)
  [ -n "$reported" ] && echo "model_reported=$reported" >> "$RUN/meta.txt"

  # 5. keep the tree, drop the private home (the sign-in copy goes with it), audit.
  #    Codex may still be writing its plugin cache under $CH/.tmp as it exits, so remove with retries
  #    and never let that stop the campaign.
  # keep Codex's own session record (it names the model and holds the full conversation), then drop the home
  roll=$( (find "$CH/sessions" -name 'rollout-*.jsonl' 2>/dev/null || true) | head -1 || true)
  [ -n "$roll" ] && cp "$roll" "$RUN/codex_session.jsonl"
  for attempt in 1 2 3 4 5; do rm -rf "$CH" 2>/dev/null && break; sleep 2; done
  [ -e "$CH/auth.json" ] && rm -f "$CH/auth.json"
  mv "$LIVE/packet" "$RUN/work" && (rm -rf "$LIVE" 2>/dev/null || true)
  python3 "$ROOT/audit_codex.py" "$RUN" || true
  if grep -q "model_not_found" "$RUN/events.jsonl" 2>/dev/null; then
    echo "run $idx: the model '$MODEL' was refused for this sign-in. A ChatGPT account offers Codex only the codex-tuned models;"
    echo "         plain gpt-5.5 needs the API: OPENAI_API_KEY=sk-... MODEL=gpt-5.5 ./campaign_codex.sh"
  fi
  if [ -d "$RUN/work/analysis_results" ]; then
    echo "run $idx: analysis_results present ($(find "$RUN/work/analysis_results" -type f | wc -l | tr -d ' ') files); $(grep -E 'turns|outside|network|blinding' "$RUN/audit.txt" | tr '\n' ';')"
  else
    echo "run $idx: no analysis_results (read events.jsonl and stderr.log)"
  fi
done
echo "done: $N runs of $ARM in $RUNS; aggregate with: python3 aggregate_runs.py --runs $ROOT/runs --out $ROOT/results"
