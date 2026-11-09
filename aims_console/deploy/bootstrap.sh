#!/usr/bin/env bash
# bootstrap.sh: a fresh Ubuntu 24.04 (or Debian 12) VPS to a running AIMS console service in one go.
#
#   On the VPS, as root:
#       curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/aims_console/deploy/bootstrap.sh -o /srv/bootstrap.sh
#       # hosted mode (the authors pay for the runs): put the Anthropic key in a file first, mode 600
#       install -m 600 /dev/null /srv/anthropic.key && nano /srv/anthropic.key     # one line: sk-ant-...
#       bash /srv/bootstrap.sh aims.leonmgarcia.com <access token> hosted
#       # or bring-your-own-key mode (every run needs a key in the page):
#       bash /srv/bootstrap.sh aims.leonmgarcia.com <access token> byok
#
# Installs Docker, opens 22/80/443, clones the console repository under /srv/aims-console (or
# updates it), writes deploy/.env, builds the image, starts the console behind Caddy (certificate
# from Let's Encrypt on first request; the host name's A record must already point at this machine)
# and prints the checks. Re-running is safe: it updates, rebuilds and restarts.
set -euo pipefail

HOST="${1:?usage: bootstrap.sh <console host name> <access token> hosted|byok [page origin]}"
TOKEN="${2:?usage: bootstrap.sh <console host name> <access token> hosted|byok [page origin]}"
MODE="${3:?usage: bootstrap.sh <console host name> <access token> hosted|byok [page origin]}"
ORIGIN="${4:-https://leonmgarcia.com}"
SRV=/srv
KEY_FILE="${AIMS_KEY_FILE:-$SRV/anthropic.key}"
REPO_URL="${AIMS_CONSOLE_REPO:-https://github.com/leongarcia2023/aims-console}"

case "$MODE" in
  hosted) [ -s "$KEY_FILE" ] || { echo "hosted mode needs the Anthropic key in $KEY_FILE (one line, mode 600)"; exit 2; }
          [ "$(stat -c %a "$KEY_FILE")" = "600" ] || { echo "$KEY_FILE must be mode 600 (chmod 600 $KEY_FILE)"; exit 2; } ;;
  byok) ;;
  *) echo "third argument must be hosted or byok"; exit 2 ;;
esac

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 git ufw unattended-upgrades ca-certificates curl
systemctl enable --now docker

echo "== firewall: ssh, http, https"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "== console source"
if [ -d "$SRV/aims-console/.git" ]; then git -C "$SRV/aims-console" pull -q; else git clone -q --depth 1 "$REPO_URL" "$SRV/aims-console"; fi
cd "$SRV/aims-console/aims_console"

echo "== deploy/.env"
{
  echo "AIMS_HOST=$HOST"
  echo "AIMS_ACCESS_TOKEN=$TOKEN"
  echo "AIMS_ORIGINS=$ORIGIN"
  echo "AIMS_MAX_CONCURRENT=${AIMS_MAX_CONCURRENT:-3}"
  echo "AIMS_MAX_TURNS_CAP=${AIMS_MAX_TURNS_CAP:-80}"
  echo "AIMS_WALL_LIMIT_MIN=${AIMS_WALL_LIMIT_MIN:-45}"
  echo "AIMS_CPUS=${AIMS_CPUS:-2}"
  echo "AIMS_MEMORY=${AIMS_MEMORY:-3g}"
  if [ "$MODE" = hosted ]; then
    echo "AIMS_HOSTED=1"
    echo "ANTHROPIC_API_KEY=$(tr -d '[:space:]' < "$KEY_FILE")"
  else
    echo "AIMS_HOSTED=0"
  fi
} > deploy/.env
chmod 600 deploy/.env

echo "== build and start (the first build downloads about 1 GB; a few minutes)"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

echo "== waiting for the service"
for i in $(seq 1 60); do
  if docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec -T console \
       python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/template', timeout=5)" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

echo "== checks"
echo -n "without the token (expect 401): "; curl -s -o /dev/null -w "%{http_code}\n" "https://$HOST/api/config" || echo "no answer yet (certificate pending? DNS?)"
echo -n "with the token (expect public true, hosted $([ "$MODE" = hosted ] && echo true || echo false)): "
curl -s -H "X-AIMS-Token: $TOKEN" "https://$HOST/api/config" | tr ',' '\n' | grep -E '"(public|hosted|byok)"' | tr '\n' ' '; echo
echo
echo "Done. Logs: docker compose -f $SRV/aims-console/aims_console/deploy/docker-compose.yml --env-file $SRV/aims-console/aims_console/deploy/.env logs -f"
echo "The page at $ORIGIN/AIMS connects on its own once this answers, provided it was built with --backend https://$HOST --token <this token>."
