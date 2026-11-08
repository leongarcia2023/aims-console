#!/usr/bin/env bash
# bootstrap.sh: a fresh Ubuntu 24.04 VPS to a running AIMS console service in one go.
#
#   On the VPS, as root, after copying the three inputs into /srv (see DEPLOY.md):
#       /srv/aims_console.zip      this console (the zip León has)
#       /srv/AIMS/                 a clone of the AIMS repository (prompts/, templates/, mcpServer/)
#       /srv/aims-examples/        the team's examples: VbgScans/, GridSpectra/, SampleLocatingImages/
#   then:
#       bash /srv/bootstrap.sh aims.leonmgarcia.com <access token> [https://leonmgarcia.com]
#
# Installs Docker, opens 22/80/443, builds the image, writes deploy/.env, starts the console behind
# Caddy (certificate from Let's Encrypt on first request; the host name's A record must already
# point at this machine), and prints the two checks. Re-running is safe: it rebuilds and restarts.
set -euo pipefail

HOST="${1:?usage: bootstrap.sh <console host name> <access token> [page origin]}"
TOKEN="${2:?usage: bootstrap.sh <console host name> <access token> [page origin]}"
ORIGIN="${3:-https://leonmgarcia.com}"
SRV=/srv

for p in "$SRV/aims_console.zip" "$SRV/AIMS/mcpServer" "$SRV/aims-examples/VbgScans"; do
  [ -e "$p" ] || { echo "missing: $p (see the header of this script)"; exit 2; }
done

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 unzip ufw unattended-upgrades ca-certificates
systemctl enable --now docker

echo "== firewall: ssh, http, https"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "== console source"
rm -rf "$SRV/aims_console"
unzip -q "$SRV/aims_console.zip" -d "$SRV"      # the zip unpacks to aims_console/
cd "$SRV/aims_console"

echo "== deploy/.env"
cat > deploy/.env <<EOF
AIMS_HOST=$HOST
AIMS_ACCESS_TOKEN=$TOKEN
AIMS_REPO=$SRV/AIMS
AIMS_EXAMPLES=$SRV/aims-examples
AIMS_ORIGINS=$ORIGIN
AIMS_MAX_CONCURRENT=4
AIMS_MAX_TURNS_CAP=80
AIMS_WALL_LIMIT_MIN=45
AIMS_CPUS=4
AIMS_MEMORY=8g
EOF
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
echo -n "with the token (expect public true): "; curl -s -H "X-AIMS-Token: $TOKEN" "https://$HOST/api/config" | head -c 120; echo
echo
echo "Done. Logs: docker compose -f $SRV/aims_console/deploy/docker-compose.yml --env-file $SRV/aims_console/deploy/.env logs -f"
echo "The page at $ORIGIN/AIMS connects on its own once this answers; no rebuild of the page is needed."
