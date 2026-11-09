#!/usr/bin/env bash
# campaign.sh: run the production arms one run at a time with retries, logging to campaign.log.
#
#   ARMS="C_litheld D_litheld_noprior E_nowindows B_nointernet" N=5 ./campaign.sh
#
# Per run: calls run_arm.sh ARM 1, then classifies the newest run directory:
#   ok      transcript present (including an agent that stopped at max turns: that is a result)
#   limit   the CLI reported a usage or rate limit, an overload or an auth failure: the run
#           directory is removed and the campaign sleeps RETRY_SLEEP before trying again
#   empty   no transcript (process killed, machine restarted): removed and retried after a
#           short pause, up to MAX_EMPTY_RETRIES times per arm
# A runner failure before the agent starts (canary reachable, integrity mismatch, probe or
# hiding check failed) stops the campaign: those are environment faults, not transient ones.
# After each kept run, the copies of data/ and literature/ inside work/ are pruned when every
# file still matches the arm manifest (the integrity check already recorded that the copy was
# exact); if anything changed they are kept as evidence.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
[ -f "$ROOT/env.sandbox.sh" ] && source "$ROOT/env.sandbox.sh"
ARMS="${ARMS:-C_litheld D_litheld_noprior E_nowindows B_nointernet}"
N="${N:-5}"
RETRY_SLEEP="${RETRY_SLEEP:-1800}"
MAX_EMPTY_RETRIES="${MAX_EMPTY_RETRIES:-4}"
LOG="$ROOT/campaign.log"
log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

classify() {   # prints ok | limit | empty for a run directory
  python3 -I - "$1" <<'PY'
import json, re, sys
from pathlib import Path
r = Path(sys.argv[1]) / "transcript.json"
if not r.exists() or r.stat().st_size == 0:
    print("empty"); sys.exit()
try:
    t = json.loads(r.read_text())
except Exception:
    print("empty"); sys.exit()
msg = str(t.get("result", "")) + " " + str(t.get("subtype", ""))
if t.get("is_error") and re.search(r"limit|rate|overload|529|quota|not logged in|authentication|unauthori[sz]ed|credit", msg, re.I) \
        and not re.search(r"max_turns", msg):
    print("limit"); sys.exit()
print("ok")
PY
}

prune() {      # drop the exact copies of data/ and literature/ from a kept run
  python3 -I - "$1" "$2" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path
man = json.load(open(sys.argv[1])); work = Path(sys.argv[2]) / "work"
for sub in ("data", "literature"):
    d = work / sub
    if not d.is_dir():
        continue
    changed = []
    for p in sorted(q for q in d.rglob("*") if q.is_file()):
        rel = str(p.relative_to(work))
        if man["sha256"].get(rel) != hashlib.sha256(p.read_bytes()).hexdigest():
            changed.append(rel)
    if changed:
        (work / f"{sub}_CHANGED.txt").write_text("\n".join(changed) + "\n")
        print(f"{sub}: {len(changed)} files differ from the manifest; kept")
    else:
        shutil.rmtree(d)
        (work / f"{sub}_PRUNED.txt").write_text(f"{sub}/ removed after the run: every file matched the arm manifest (sha256).\n")
        print(f"{sub}: pruned")
PY
}

n_ok() { find "$ROOT/runs/$1" -maxdepth 2 -name transcript.json -size +0 2>/dev/null | wc -l; }

log "campaign start: arms=[$ARMS] N=$N model=${MODEL:-?} sandbox=${SANDBOX:-?}"
T0=$(date +%s)
BUDGET_MIN="${BUDGET_MIN:-0}"     # >0: start no new run after this many minutes (the harness's 2 h job limit; a relaunch resumes)
for ARM in $ARMS; do
  empties=0
  while [ "$(n_ok "$ARM")" -lt "$N" ]; do
    if [ "$BUDGET_MIN" -gt 0 ] && [ $(( ($(date +%s) - T0) / 60 )) -ge "$BUDGET_MIN" ]; then
      log "budget of $BUDGET_MIN min reached before $ARM run $(( $(n_ok "$ARM") + 1 )); exiting cleanly (relaunch to resume)"; exit 0
    fi
    log "$ARM: starting run $(( $(n_ok "$ARM") + 1 )) of $N"
    ./run_arm.sh "$ARM" 1 >> "$LOG" 2>&1; rc=$?
    newest=$(find "$ROOT/runs/$ARM" -maxdepth 1 -type d -name 'run_*' 2>/dev/null | sort | tail -1)
    if [ -z "$newest" ] || [ ! -e "$newest/meta.txt" ]; then
      log "$ARM: runner failed before the agent started (rc=$rc); stopping the campaign"
      [ -n "$newest" ] && [ ! -e "$newest/transcript.json" ] && rm -rf "$newest"
      exit 1
    fi
    st=$(classify "$newest")
    case "$st" in
      ok)
        cost=$(python3 -I -c "import json;t=json.load(open('$newest/transcript.json'));print(t.get('total_cost_usd'),t.get('num_turns'),round((t.get('duration_ms') or 0)/60000,1),t.get('subtype'))" 2>/dev/null)
        log "$ARM: $(basename "$newest") done (cost turns minutes subtype: $cost)"
        prune "$ROOT/arms/manifests/$ARM.json" "$newest" | sed "s/^/$(date -Is) $ARM: /" | tee -a "$LOG"
        ;;
      limit)
        log "$ARM: $(basename "$newest") hit a usage/rate limit or auth error; removing it and sleeping $RETRY_SLEEP s"
        rm -rf "$newest"; sleep "$RETRY_SLEEP"
        ;;
      empty)
        empties=$((empties + 1))
        log "$ARM: $(basename "$newest") has no transcript (attempt $empties of $MAX_EMPTY_RETRIES); removing it"
        rm -rf "$newest"
        if [ "$empties" -ge "$MAX_EMPTY_RETRIES" ]; then log "$ARM: too many empty runs; stopping the campaign"; exit 1; fi
        sleep 600
        ;;
    esac
  done
  log "$ARM: $N runs complete"
done
log "campaign finished"
