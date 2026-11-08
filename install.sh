#!/usr/bin/env bash
# install.sh: fetch the AIMS console and the AIMS code into ./aims-console-local and start the
# console on this computer. Usage, from any directory:
#
#   curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/install.sh | bash
#
# Needs git and Python 3.12. Nothing is sent anywhere but your own model provider, with your key.
set -euo pipefail
REPO_URL="${AIMS_CONSOLE_REPO:-https://github.com/leongarcia2023/aims-console}"
DIR="${AIMS_DIR:-$PWD/aims-console-local}"
command -v git >/dev/null || { echo "git is needed"; exit 1; }
mkdir -p "$DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q --depth 1 "$REPO_URL" "$DIR"; fi
exec "$DIR/aims_console/reviewer.sh"
