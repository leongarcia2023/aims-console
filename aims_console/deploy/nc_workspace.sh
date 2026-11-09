#!/usr/bin/env bash
# nc_workspace.sh: run the AIMS console inside a National Compute Marshall workspace (for members of
# an organization that has one, such as the Shen laboratory). Open the console in a browser, go to
# Marshall, add a Terminal pane, and run:
#
#   curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/aims_console/deploy/nc_workspace.sh | bash
#
# It installs a Python 3.12 build for your user (uv, no sudo needed), clones or updates the console
# under /data/workspace/aims-console-local (the workspace disk, which survives restarts), and starts
# the console in a tmux session named "aims" on port 8080 in bring-your-own-key mode. Then click the
# http://localhost:8080 link it prints: the workspace opens the port under the /port/8080/ route of
# your workspace address, reachable through your console sign-in. Your model key goes in the page, as
# on a laptop; the workspace holds no key and bills nothing for model calls (see NC_WORKSPACE.md).
#
#   tmux attach -t aims            # watch it; Ctrl-B then D detaches
#   tmux kill-session -t aims      # stop it
#   tail -f /data/workspace/aims-console.log
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
WS="${AIMS_WS:-/data/workspace}"
PORT="${PORT:-8080}"
REPO_URL="${AIMS_CONSOLE_REPO:-https://github.com/leongarcia2023/aims-console}"
cd "$WS"
if ! command -v uv >/dev/null; then
  command -v pipx >/dev/null || { echo "pipx is needed to install uv (it is preinstalled in Marshall workspaces)"; exit 1; }
  pipx install uv >/dev/null
fi
uv python install 3.12 >/dev/null 2>&1 || true
PY="$(uv python find 3.12)"
[ -x "$PY" ] || { echo "no Python 3.12 after 'uv python install 3.12'"; exit 1; }
if [ -d "$WS/aims-console-local/.git" ]; then
  git -C "$WS/aims-console-local" pull -q
else
  git clone -q --depth 1 "$REPO_URL" "$WS/aims-console-local"
fi
tmux kill-session -t aims 2>/dev/null || true
tmux new -d -s aims "PYTHON=$PY PORT=$PORT $WS/aims-console-local/aims_console/reviewer.sh > $WS/aims-console.log 2>&1"
echo "AIMS console starting in tmux session 'aims' (the first start installs the requirements, a few minutes)."
echo "Log: $WS/aims-console.log"
echo "Open: http://localhost:$PORT"
echo "(click that link in this pane; the workspace serves it at .../port/$PORT/ of your workspace address)"
