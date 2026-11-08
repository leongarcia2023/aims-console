# Hosting the public console (optional)

Decision (León, 2026-11-08): no hosted service. Reviewers run the console on their own machines
(`reviewer.sh`, `install.sh`, the Docker image at the repository root), so nobody but the person
running it pays for anything. What follows is the recipe for a hosted copy, kept in case it is
ever wanted; nothing in it is in use.

The reviewer's address is `leonmgarcia.com/AIMS` and nothing else: the page there is static
(GitHub Pages) and already carries the service's address and access token inside its encrypted
body, so once the service answers, the page connects on its own. The service itself has to live
on a machine that is not GitHub Pages, and a browser will only talk to it over HTTPS under a
name, so it gets one subdomain, `aims.leonmgarcia.com`, which no reviewer ever types or sees:
the page does not show it, and the gate does not contain it. If even that is unwanted, the API
can sit under `leonmgarcia.com/AIMS/api` instead, but only with a proxy in front of the whole
domain (Cloudflare with the DNS moved there, routing that path to the VPS and stripping the
`/AIMS` prefix) or with the site itself served from the VPS instead of GitHub Pages; both are
larger changes to the site than one DNS record, and the page supports a path-prefixed service
if that is ever chosen (`build_static.py --backend https://leonmgarcia.com/AIMS`).

## The twenty minutes

1. Create the VPS: Ubuntu 24.04, 4 vCPU, 8 GB, 80 GB disk (Hetzner CPX31 or CX32, DigitalOcean
   4 vCPU / 8 GB, Linode 8 GB; about 15 to 50 USD a month at 2026 prices, check the provider),
   with your SSH key. Note its IPv4 address.
2. At the registrar of leonmgarcia.com, add one record: `A  aims  <that IPv4>` (TTL short).
   Nothing else on the domain changes; GitHub Pages keeps serving the site.
3. Copy the three inputs to the VPS:
   ```bash
   scp aims_console.zip root@<ip>:/srv/
   scp aims_console/deploy/bootstrap.sh root@<ip>:/srv/
   rsync -a /path/to/examples/ root@<ip>:/srv/aims-examples/      # VbgScans, GridSpectra, SampleLocatingImages (23 MB)
   ssh root@<ip> git clone <AIMS repository> /srv/AIMS             # with your own GitHub credentials
   ```
4. Run the bootstrap with the host name and the access token the page was built with (the
   token is in the Claude conversation of 2026-11-07, not in any file here):
   ```bash
   ssh root@<ip> bash /srv/bootstrap.sh aims.leonmgarcia.com <token>
   ```
   It installs Docker, opens 22/80/443, writes `deploy/.env`, builds the image (about 1 GB of
   downloads), starts the console behind Caddy, and prints two checks: `401` without the token
   and `{"sim": true, ... "public": true` with it. Caddy fetches the certificate on the first
   request, which needs step 2 to have propagated.
5. Open `leonmgarcia.com/AIMS`, enter the page password: the note under the title should no
   longer say the service is unreachable, and the model list and key field are live. Do one
   Haiku run with your own key.
6. After the review: delete the VPS and remove the DNS record. The page falls back to replaying
   the recorded runs by itself.

## What the service needs (for anyone redoing this elsewhere)

* Linux with Docker (or Python 3.12 and `requirements.txt`). Four cores and 8 GB are
  comfortable for four concurrent runs; the particle filter is the load, one to two minutes of
  one core per position estimate.
* A host name whose A record points at it and ports 80 and 443 open, so Caddy can get a
  certificate. The page is served over HTTPS, and browsers refuse a plain-http API from an https
  page.
* A clone of the AIMS repository (prompts, template, `mcpServer/`) and the team's `examples`
  directory (`VbgScans/`, `GridSpectra/`, `SampleLocatingImages/`; the public repository ships
  only the two markdown examples, the data is in the team's zip).
* The access token: the service accepts API calls only with it; the static page carries it
  inside its encrypted body, so the token is known to whoever has the page's password and to
  nobody else. Without the token the service is a closed door; with it but without an API key it
  still starts nothing.

Changing the host name or the token means rebuilding and re-pushing the page:

```bash
python static/build_static.py --repo /path/to/AIMS --runs runs_console \
    --out /path/to/leongarcia2023.github.io/AIMS --password <page password> \
    --backend https://aims.leonmgarcia.com --token <token> --include <run ids>
cd /path/to/leongarcia2023.github.io && git add AIMS && git commit -m "AIMS console page" && git push
```

## What the public service does and does not do

* Every run starts its own simulated MCP server on a loopback port under the run's directory
  (`<run>/sim/sessions`), seeded differently, and stops it when the run ends. Runs never share
  an instrument state.
* A run must bring an API key (Anthropic or OpenAI). The key goes into the run's process
  environment (Claude driver) or the request headers of the run's own calls (OpenAI driver). It
  is not written to `meta.json`, `events.jsonl` or the service log; `meta.json` records only
  `own_key: true`. The server's own credentials are removed from the environment at startup in
  public mode, so a run without a key gets a 400 and nothing else.
* Caps: turns (`--max-turns-cap`, 80), wall time (`--wall-limit-min`, 45; the watchdog stops the
  run and marks it), concurrent runs (`--max-concurrent`, 4; the fifth gets a 429 and a message
  to try again later).
* A run is visible only to the browser that started it (a random client id in that browser's
  local storage); the runs list and the WebSocket are filtered by it.
* The discovery loop is not offered: it needs file and shell tools on the server.
* CORS: only the origins in `--origins` may call the API from a page. The access token is the
  actual lock.
* Every run's log and simulator session stays under `/runs` (the `runs` volume). Prune now and
  then: `docker compose exec console find /runs -maxdepth 1 -mtime +30 -type d -exec rm -rf {} +`.
* The microscope PC is never the host: the public service runs simulators only.

## Updating the service

```bash
scp aims_console.zip root@<ip>:/srv/ && ssh root@<ip> bash /srv/bootstrap.sh aims.leonmgarcia.com <token>
```

(the bootstrap re-unpacks, rebuilds and restarts; `deploy/.env` is rewritten from its arguments).
Logs: `docker compose -f /srv/aims_console/deploy/docker-compose.yml --env-file /srv/aims_console/deploy/.env logs -f`.

## Alternatives, for the record

A lab machine behind a Cloudflare Tunnel (free, no open ports, but reviewer traffic enters the
lab network and the machine must stay on), or a cluster with public ingress. Without Docker:
`pip install -r requirements.txt`, then
`AIMS_ACCESS_TOKEN=<token> ./run_console.sh --repo /srv/AIMS --examples /srv/aims-examples --runs /srv/aims-runs --sim-per-run --public --host 127.0.0.1 --port 8080 --origins https://leonmgarcia.com`
behind any HTTPS reverse proxy (Caddy: `aims.leonmgarcia.com { reverse_proxy 127.0.0.1:8080 }`).
