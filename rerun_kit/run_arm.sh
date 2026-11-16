#!/usr/bin/env bash
# run_arm.sh ARM N
#   Run the discovery loop (pass 1) N times inside fresh copies of arms/ARM with the headless
#   claude CLI, one private environment per run.
#
# Requirements: the claude CLI, authentication (ANTHROPIC_API_KEY exported, or the CLI's
# credentials file in the real HOME, which is copied alone into the run's private HOME), and
# the Python dependencies of the released analysis tools installed (PY_ENV_BIN).
#
# Network policy (this script does NOT enforce egress by itself):
#   arm A           web tools allowed
#   arms B to E     web tools removed here (--disallowedTools), and the machine or container
#                   must sit behind an egress allowlist that permits only the model API endpoint.
#                   The script records a canary (can https://example.com be reached?) per run so
#                   the no-internet claim in the paper is backed by a log, and audit_run.py lists
#                   every pip/curl/wget/git command the agent issued.
#
# Blinding (three leaks found in the pilot, all closed here):
#   1. an in-tree manifest named the withheld papers -> manifests live in arms/manifests/, and
#      build_arms.py refuses to build a tree that mentions the ablation;
#   2. the working path contained the arm name -> each run works in LIVE_ROOT/<id>/packet (a
#      neutral path) and the tree is moved into runs/ARM/run_NN/work only after the agent exits;
#   3. the nested CLI inherited the parent session's environment (claude.ai Project binding with
#      the paper and the as-run results in its document list, user memory, the parent session id,
#      and tools such as file delivery into the parent chat) -> the CLI now starts with an
#      explicit environment allowlist (env -i) and a fresh private HOME per run, and a one-turn
#      probe (probe.json) is run before each arm run; the run is refused if the probe reports an
#      attached Project, a memory snapshot, or a tool outside the file/shell set;
#   4. the pilot agent ran `find /` and located the as-run analysis directory on the same machine
#      -> with SANDBOX=1 (default) every CLI process runs under sandbox_exec.sh, which hides the
#      directories listed in HIDE_PATHS (the original run, the paper, the parent session's files)
#      behind empty mounts and drops the capability to undo that; the runner verifies from inside
#      the sandbox that every listed path is empty (hidden.txt) before each run. A clean container
#      per run that holds only the arm tree is the stronger option; then SANDBOX=0.
#
# The agent's full log (every message, tool call and result) is copied to run_NN/agent_log.jsonl
# and audited by audit_run.py into audit.json / audit.txt.
set -euo pipefail
ARM="${1:?arm name, e.g. B_nointernet}"
N="${2:-10}"
MODEL="${MODEL:-claude-opus-4-8}"      # the as-run model; override with MODEL=...
PROBE_MODEL="${PROBE_MODEL:-claude-haiku-5-5}"
MAX_TURNS="${MAX_TURNS:-400}"
LIVE_ROOT="${LIVE_ROOT:-/tmp/mim_runs}"  # neutral path; must not mention the ablation
SANDBOX="${SANDBOX:-1}"
HIDE_PATHS="${HIDE_PATHS:-}"            # colon-separated; required when SANDBOX=1
ROOT="$(cd "$(dirname "$0")" && pwd)"
ARMDIR="$ROOT/arms/$ARM"
MANIFEST="$ROOT/arms/manifests/$ARM.json"
RUNS="$ROOT/runs/$ARM"
REAL_HOME="$HOME"
[ -d "$ARMDIR" ] || { echo "no such arm: $ARMDIR (run build_arms.py first)"; exit 1; }
[ -f "$MANIFEST" ] || { echo "no manifest: $MANIFEST (rebuild the arms)"; exit 1; }
[ -e "$ARMDIR/manifest.json" ] && { echo "in-tree manifest.json found in $ARMDIR; rebuild the arms"; exit 1; }
case "$LIVE_ROOT" in *ablation*|*litheld*|*noprior*|*nowindows*|*asrun*|*nointernet*) echo "LIVE_ROOT must be a neutral path: $LIVE_ROOT"; exit 1;; esac
WORK_MOUNT="${WORK_MOUNT:-/tmp/mim_live}"  # where a sandboxed run sees itself (same for every run)
if [ "$SANDBOX" = 1 ]; then
  [ -n "$HIDE_PATHS" ] || { echo "SANDBOX=1 needs HIDE_PATHS (colon-separated paths to hide: the original run, the paper, this session's files); or SANDBOX=0 inside a clean container"; exit 1; }
  case ":$HIDE_PATHS:" in *":$ROOT:"*|*":$(dirname "$ROOT"):"*) ;; *) echo "HIDE_PATHS must include this directory or its parent ($ROOT holds the arms, manifests and finished runs)"; exit 1;; esac
  # the real run directory is bind-mounted onto WORK_MOUNT and LIVE_ROOT is hidden, so a run never
  # sees the sibling runs (other arms) next to it; the CLI's temp directory (/tmp/claude-<uid>,
  # where a parent session keeps its scratch files) is replaced by an empty writable one
  case ":$HIDE_PATHS:" in *":/tmp/claude-$(id -u):"*) echo "do not put /tmp/claude-$(id -u) in HIDE_PATHS; it is replaced by a fresh tmpfs automatically (FRESH_PATHS)"; exit 1;; esac
  HIDE_PATHS="$HIDE_PATHS:$LIVE_ROOT"
  mkdir -p "$WORK_MOUNT"
  SBX=("$ROOT/sandbox_exec.sh")
  SEEN="$WORK_MOUNT"           # the run's directory as the agent sees it
else
  SBX=()
fi

PASS="${PASS:-1}"                       # 2 = the second pass: its prompt section, and the pass-1 results stay in the tree
if [ "$PASS" = 2 ]; then
  PROMPT="$(sed -n '/## Second pass/,$p' "$ARMDIR/Discovery loop prompt.md" | sed '1d')"
  [ -d "$ARMDIR/analysis_results" ] || { echo "PASS=2 needs analysis_results/ in the arm tree (build_pass2_arms.py)"; exit 1; }
else
  # the first-pass section: from its heading to the next heading or the end of the file (the
  # first-pass-only arms have no "## Second pass" marker; the old range + '$d' emptied the prompt)
  PROMPT="$(awk '/^## First pass/{f=1;next} /^## /{if(f)exit} f' "$ARMDIR/Discovery loop prompt.md")"
  [ -n "$(echo "$PROMPT" | tr -d '[:space:]')" ] || { echo "empty first-pass prompt extracted from $ARMDIR/Discovery loop prompt.md"; exit 1; }
fi
TOOLS=(Bash Read Write Edit Glob Grep)
if [ "$ARM" = "A_asrun" ]; then
  TOOLS+=(WebSearch WebFetch)
  DISALLOW=()
else
  DISALLOW=(--disallowedTools WebSearch WebFetch)
fi

# Environment allowlist for the nested CLI: nothing from the parent session except what
# authentication and the network path need. Add variables here, never by inheritance.
env_for_run() {   # usage: env_for_run HOME ; prints KEY=VALUE lines
  local home="$1"
  echo "HOME=$home"
  echo "PATH=${PY_ENV_BIN:+$PY_ENV_BIN:}$PATH"
  echo "TERM=dumb"
  echo "LANG=${LANG:-C.UTF-8}"
  for v in ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN \
           HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy \
           SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE NODE_EXTRA_CA_CERTS NODE_OPTIONS \
           MPLBACKEND OMP_NUM_THREADS; do
    [ -n "${!v:-}" ] && echo "$v=${!v}"
  done
  echo "MPLBACKEND=${MPLBACKEND:-Agg}"
}

make_home() {   # usage: make_home DIR ; a private HOME with only credentials and the skill
  local home="$1"
  mkdir -p "$home/.claude/skills"
  if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$REAL_HOME/.claude/.credentials.json" ]; then
    cp "$REAL_HOME/.claude/.credentials.json" "$home/.claude/.credentials.json"
  fi
  # the as-run setup had the discovery skill installed in Claude Desktop; install it under the
  # name the prompt uses so the agent's "use mim_discovery_skill" resolves the same way
  cp -a "$ARMDIR/mim-discovery-skill" "$home/.claude/skills/mim_discovery_skill"
}

PROBE_Q='Answer in strict JSON only, no prose and no code fence, with keys: tools (every tool name you can call, deferred ones included), skills (every skill name available to you), project_attached (true if a claude.ai Project or a project document list is attached to this session), memory_present (true if a user memory snapshot, profile or preferences block is in your context), user_identity (any user name or email given to you, else null).'

mkdir -p "$RUNS" "$LIVE_ROOT"
existing=$(find "$RUNS" -maxdepth 1 -type d -name 'run_*' | wc -l)
for i in $(seq 1 "$N"); do
  idx=$((existing + i))
  RUN="$RUNS/run_$(printf %02d "$idx")"
  mkdir -p "$RUN"
  ID="$(date +%s)_$RANDOM"
  LIVE="$LIVE_ROOT/$ID"
  mkdir -p "$LIVE/packet" && cp -a "$ARMDIR/." "$LIVE/packet/"
  [ "$PASS" = 2 ] || rm -rf "$LIVE/packet/analysis_results"
  make_home "$LIVE/home"
  SEEN="${SEEN:-$LIVE}"            # SANDBOX=0: the agent sees the real path
  [ "$SANDBOX" = 1 ] && SEEN="$WORK_MOUNT"
  mapfile -t ENVLINES < <(env_for_run "$SEEN/home")
  # every CLI process of this run: hide the host, show the run at $SEEN, start inside the packet
  SBXENV=(HIDE_PATHS="$HIDE_PATHS" FRESH_PATHS="${FRESH_PATHS:-/tmp/claude-$(id -u)}" EXPOSE_SRC="$LIVE" EXPOSE_DST="$WORK_MOUNT" CHDIR="$SEEN/packet")

  # 1. integrity: the copied tree must match the manifest exactly
  python3 - "$MANIFEST" "$LIVE/packet" > "$RUN/integrity.txt" <<'PY'
import hashlib, json, sys
from pathlib import Path
man = json.load(open(sys.argv[1])); root = Path(sys.argv[2])
files = sorted(p for p in root.rglob("*") if p.is_file())
bad = []
for p in files:
    rel = str(p.relative_to(root))
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if man["sha256"].get(rel) != h:
        bad.append(rel)
missing = sorted(set(man["sha256"]) - {str(p.relative_to(root)) for p in files})
print(f"files={len(files)} manifest={man['n_files']} mismatched={len(bad)} missing={len(missing)}")
if bad or missing or len(files) != man["n_files"]:
    print("MISMATCH", bad[:5], missing[:5]); sys.exit(3)
print("tree matches manifest")
PY

  # 2. canary: is the open internet reachable from here?
  if curl -s -m 5 -o /dev/null https://example.com; then
    echo "canary: internet reachable" > "$RUN/canary.txt"
  else
    echo "canary: no internet" > "$RUN/canary.txt"
  fi
  if [ "$ARM" != "A_asrun" ] && grep -q reachable "$RUN/canary.txt"; then
    echo "run $idx: internet is reachable but arm $ARM requires egress blocked; aborting" | tee -a "$RUN/meta.txt"
    rm -rf "$LIVE"; exit 2
  fi

  # 3a. hiding: from inside the sandbox, every path in HIDE_PATHS must be empty or absent
  if [ "$SANDBOX" = 1 ]; then
    env "${SBXENV[@]}" "${SBX[@]}" bash -c '
      IFS=: read -r -a ps <<< "$HIDE_PATHS"; bad=0
      for p in "${ps[@]}"; do [ -n "$p" ] || continue
        if [ -d "$p" ] && [ -n "$(ls -A "$p" 2>/dev/null)" ]; then echo "VISIBLE: $p"; bad=1
        elif [ -f "$p" ] && [ -s "$p" ]; then echo "VISIBLE: $p"; bad=1
        else echo "hidden: $p"; fi
      done
      [ -f "$PWD/Physics_prior.md" ] && echo "run visible at $PWD" || { echo "RUN NOT VISIBLE at $PWD"; bad=1; }
      exit $bad' > "$RUN/hidden.txt" || { echo "run $idx: hiding failed:"; grep VISIBLE "$RUN/hidden.txt"; rm -rf "$LIVE"; exit 6; }
  else
    echo "SANDBOX=0: host filesystem not hidden (acceptable only inside a clean container)" > "$RUN/hidden.txt"
  fi

  # 3b. probe: what does a CLI started in this environment see? (one cheap turn, no tools)
  ( cd "$LIVE/packet" && env "${SBXENV[@]}" "${SBX[@]}" env -i "${ENVLINES[@]}" claude -p "$PROBE_Q" --model "$PROBE_MODEL" \
      --allowedTools "${TOOLS[@]}" "${DISALLOW[@]}" --output-format json --max-turns 1 \
      > "$RUN/probe.json" 2> "$RUN/probe.err" ) || true
  python3 - "$RUN/probe.json" "$ARM" > "$RUN/probe.txt" <<'PY' || { cat "$RUN/probe.txt"; rm -rf "$LIVE"; exit 4; }
import json, re, sys
t = json.load(open(sys.argv[1])); arm = sys.argv[2]
r = t.get("result", "")
r = re.sub(r"^```(json)?|```$", "", r.strip(), flags=re.M).strip()
try:
    a = json.loads(r[r.find("{"):r.rfind("}") + 1])
except Exception:
    print("probe answer not parseable:", r[:300]); sys.exit(5)
tools = [str(x).split(" ")[0] for x in a.get("tools", [])]
bad = [x for x in tools if x.startswith("mcp__") or x in ("Projects", "SendUserFile", "SendUserMessage", "Artifact")]
if arm != "A_asrun":
    bad += [x for x in tools if x in ("WebSearch", "WebFetch")]
problems = []
if a.get("project_attached"): problems.append("a claude.ai Project is attached")
if a.get("memory_present"): problems.append("a user memory snapshot is present")
if a.get("user_identity"): problems.append(f"user identity visible: {a.get('user_identity')}")
if bad: problems.append(f"tools outside the allowed surface: {bad}")
print(f"probe session={t.get('session_id')} cost={t.get('total_cost_usd')} tools={len(tools)} skills={a.get('skills')}")
print("project_attached", a.get("project_attached"), "memory_present", a.get("memory_present"), "user_identity", a.get("user_identity"))
if problems:
    print("PROBE FAILED:", "; ".join(problems)); sys.exit(5)
print("probe clean")
PY

  echo "model=$MODEL arm=$ARM web_tools=$([ "$ARM" = A_asrun ] && echo on || echo off) sandbox=$SANDBOX live_id=$ID packet=$SEEN/packet start=$(date -Is)" > "$RUN/meta.txt"
  # 4. the run, in the scrubbed environment, the private HOME and (SANDBOX=1) the hidden-host sandbox
  ( cd "$LIVE/packet" && env "${SBXENV[@]}" "${SBX[@]}" env -i "${ENVLINES[@]}" claude -p "$PROMPT" --model "$MODEL" \
      --allowedTools "${TOOLS[@]}" "${DISALLOW[@]}" --output-format json --max-turns "$MAX_TURNS" \
      > "$RUN/transcript.json" 2> "$RUN/stderr.log" ) || echo "run $idx exited nonzero" >> "$RUN/meta.txt"
  echo "end=$(date -Is)" >> "$RUN/meta.txt"

  # 5. keep the agent's own log, then move the tree out of the neutral path
  logf=$(ls -t "$LIVE"/home/.claude/projects/*/*.jsonl 2>/dev/null | head -1 || true)
  [ -n "$logf" ] && cp "$logf" "$RUN/agent_log.jsonl"
  mv "$LIVE/packet" "$RUN/work" && rm -rf "$LIVE"
  python3 "$ROOT/audit_run.py" "$RUN" > /dev/null || true
  if [ -d "$RUN/work/analysis_results" ]; then
    echo "run $idx: analysis_results present ($(find "$RUN/work/analysis_results" -type f | wc -l) files); $(grep -E 'blinding mentions|unexpected tools|pip installs' "$RUN/audit.txt" | tr '\n' ';')"
  else
    echo "run $idx: no analysis_results (inspect transcript.json)"
  fi
done
echo "done: $N runs of $ARM in $RUNS; aggregate with: python aggregate_runs.py --runs $ROOT/runs --out $ROOT/results"
