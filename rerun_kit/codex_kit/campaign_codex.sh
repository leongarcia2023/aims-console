#!/usr/bin/env bash
# campaign_codex.sh: run_arm_codex.sh for one or more arms, N runs each, resumable (a run counts
# when its transcript.json exists and is not empty). Writes campaign_codex.log beside this file.
#
#   MODEL=gpt-5.5 ARMS="C_litheld" N=2 ./campaign_codex.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ARMS="${ARMS:-C_litheld}"
N="${N:-2}"
MODEL="${MODEL:-gpt-5.5}"
LOG="$ROOT/campaign_codex.log"
ts() { date +%Y-%m-%dT%H:%M:%S%z; }
n_ok() {   # finished runs: a transcript whose subtype is success (a refused model or an API error does not count)
  python3 - "$ROOT/runs/$1" <<'PY'
import json, sys
from pathlib import Path
n = 0
for t in Path(sys.argv[1]).glob("run_*/transcript.json"):
    try:
        n += json.load(open(t)).get("subtype") == "success"
    except Exception:
        pass
print(n)
PY
}
echo "$(ts) campaign start: arms=[$ARMS] N=$N model=$MODEL driver=codex" | tee -a "$LOG"
for ARM in $ARMS; do
  while [ "$(n_ok "$ARM")" -lt "$N" ]; do
    done_n=$(n_ok "$ARM")
    echo "$(ts) $ARM: starting run $((done_n + 1)) of $N" | tee -a "$LOG"
    MODEL="$MODEL" "$ROOT/run_arm_codex.sh" "$ARM" 1 2>&1 | tee -a "$LOG" | sed 's/^/    /'
    rc=${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then echo "$(ts) $ARM: runner failed (exit $rc); stopping this arm" | tee -a "$LOG"; break; fi
    new_n=$(n_ok "$ARM")
    if [ "$new_n" -le "$done_n" ]; then
      # the run did not finish (refused model, API error, no transcript): keep its directory, stop the arm
      echo "$(ts) $ARM: run did not finish (see the newest runs/$ARM/run_*/audit.txt and stderr.log); stopping this arm" | tee -a "$LOG"; break
    fi
    last=$(ls -d "$ROOT/runs/$ARM"/run_* | sort | tail -1)
    python3 - "$last" <<'PY' | tee -a "$LOG"
import json, sys, datetime
from pathlib import Path
r = Path(sys.argv[1]); t = json.load(open(r / "transcript.json"))
u = t.get("usage") or {}
print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {r.parent.name}: {r.name} done: subtype={t.get('subtype')} turns={t.get('num_turns')} "
      f"minutes={(t.get('duration_ms') or 0)/60000:.1f} tokens in/cached/out={u.get('input_tokens')}/{u.get('cache_read_input_tokens')}/{u.get('output_tokens')}")
PY
  done
done
echo "$(ts) campaign finished" | tee -a "$LOG"
