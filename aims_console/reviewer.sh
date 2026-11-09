#!/usr/bin/env bash
# reviewer.sh: run the AIMS console on this computer, on your own API key, against a simulated
# instrument. No account with the authors, nothing leaves this machine except your own model calls.
#
#   ./reviewer.sh            # first run creates .venv and installs the requirements (a few minutes)
#
# Needs Python 3.11 or 3.12 (python3.12 or python3.11 on PATH, or set PYTHON=/path/to/one) and the
# AIMS code next to this directory (../aims: mcpServer/, prompts/, templates/, examples/), which
# install.sh fetches. Newer Pythons are refused: pySPM, which reads the microscope's files, does not
# build on them yet.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${AIMS_REPO:-$HERE/../aims}"
PORT="${PORT:-8080}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)'; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.11 or 3.12 is needed (python3.12 or python3.11 on PATH, or PYTHON=/path/to/it). pySPM, which reads the microscope's files, does not build on newer Pythons yet."; exit 1; }
[ -d "$REPO/mcpServer" ] || { echo "the AIMS code is not at $REPO (expected mcpServer/, prompts/, templates/, examples/); run install.sh, or set AIMS_REPO"; exit 1; }
if [ ! -x "$HERE/.venv/bin/python" ]; then
  echo "== creating .venv and installing the requirements (once)"
  "$PY" -m venv "$HERE/.venv"
  "$HERE/.venv/bin/pip" install -q --upgrade pip
  "$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt"
fi
EXAMPLES="$REPO/examples"
[ -d "$EXAMPLES/VbgScans" ] || echo "note: $EXAMPLES has no VbgScans/GridSpectra; navigation runs work, measurement runs will find no recorded sweeps"
echo "== AIMS console on http://localhost:$PORT  (your own API key goes in the page; Ctrl-C stops it)"
( sleep 3; command -v open >/dev/null && open "http://localhost:$PORT" || command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT" || true ) &
# a clean environment: nothing of this shell reaches the agent processes except the path, the
# locale, proxy settings and the console's own AIMS_* switches (AIMS_OPENAI_URL for tests)
KEEP=(HOME="$HOME" PATH="$HERE/.venv/bin:$PATH" LANG="${LANG:-C.UTF-8}" TERM="${TERM:-dumb}")
for v in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy SSL_CERT_FILE REQUESTS_CA_BUNDLE AIMS_OPENAI_URL AIMS_SIM; do
  eval "val=\${$v:-}"; [ -n "$val" ] && KEEP+=("$v=$val")
done
exec env -i "${KEEP[@]}" "$HERE/.venv/bin/python" -m backend.app --repo "$REPO" --examples "$EXAMPLES" --runs "$HERE/runs_local" \
     --sim-per-run --public --host 127.0.0.1 --port "$PORT"
