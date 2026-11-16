#!/usr/bin/env bash
# run_console.sh: start the AIMS console under a clean environment (credentials, proxy and PATH
# only), so the agent subprocesses inherit nothing else from the operator's shell.
#   ./run_console.sh --repo /path/to/AIMS --mcp-url http://localhost:8000/sse [--sim] [--port 8080] [--model ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
KEEP=(PATH HOME TERM LANG ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
      HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy SSL_CERT_FILE REQUESTS_CA_BUNDLE
      NODE_EXTRA_CA_CERTS NODE_OPTIONS AIMS_SIM AIMS_OPENAI_URL AIMS_ACCESS_TOKEN PYTHON)
ENVLINES=()
for v in "${KEEP[@]}"; do [ -n "${!v:-}" ] && ENVLINES+=("$v=${!v}"); done
cd "$HERE"
exec env -i "${ENVLINES[@]}" "$PY" -m backend.app "$@"
