# Hosting the console behind leonmgarcia.com/AIMS

Decision (León, 2026-11-09): the page at `leonmgarcia.com/AIMS` should run AIMS when a reviewer
edits the prompt and presses Start, with nothing to install and no key to bring. That needs a
service somewhere a browser can reach over HTTPS, and a model key the service pays with. This file
is the recipe; `bootstrap.sh` does the machine part in one command.

Two modes, one switch (`AIMS_HOSTED` in `deploy/.env`, the third argument of `bootstrap.sh`):

* `hosted`: the service holds one Anthropic key and every run uses it, within the caps (turns, wall
  time, concurrent runs). The page asks for no key. Whoever has the page password can spend on that
  key, so it should be a key of its own with a monthly spend limit set in the Anthropic Console
  (Settings, Limits) and revocable without touching anything else. GPT models still need the
  reviewer's own OpenAI key (the field appears only when a GPT model is chosen).
* `byok`: the service lends no credential; every run needs a key entered in the page (the 2026-11-07
  design). Same caps, same isolation.

The reviewer's address is `leonmgarcia.com/AIMS` and nothing else: the page there is static
(GitHub Pages) and carries the service's address and access token inside its encrypted body, so once
the service answers, the page connects on its own. The service lives on a machine that is not GitHub
Pages, under one subdomain, `aims.leonmgarcia.com`, which no reviewer types or sees. A National
Compute workspace cannot be that machine: its ports open only to the owner's own signed-in browser
(see NC_WORKSPACE.md), and the lab's GPU cluster bills whole GPU nodes.

## The twenty minutes

1. Create the VPS: Ubuntu 24.04, 2 vCPU, 8 GB, 40 GB disk, with your SSH key. Note its IPv4
   address. The 4 GB sizes are too small: in one GP regression (step 7 of the navigation prompt)
   the simulator peaks at about 5.4 GB (measured 2026-11-12), and the kernel kills it. More cores
   shorten the regression (5 to 9 minutes on two).
2. At the registrar of leonmgarcia.com, add one record: `A  aims  <that IPv4>` (TTL short).
   Nothing else on the domain changes; GitHub Pages keeps serving the site.
3. For hosted mode, make the key: in the Anthropic Console create a key for this purpose (its own
   workspace, a monthly spend limit you are comfortable losing, e.g. 100 USD), then on the VPS:
   ```bash
   install -m 600 /dev/null /srv/anthropic.key && nano /srv/anthropic.key     # one line: sk-ant-...
   ```
   The key never goes on a command line or into the repository; `bootstrap.sh` reads this file into
   `deploy/.env` (mode 600) and the container's environment, and the console passes it only to the
   agent subprocess of each run.
4. Run the bootstrap with the host name, the access token the page will be built with
   (`python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`), and the mode:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/aims_console/deploy/bootstrap.sh -o /srv/bootstrap.sh
   bash /srv/bootstrap.sh aims.leonmgarcia.com <token> hosted
   ```
   It installs Docker, opens 22/80/443, clones the console repository to `/srv/aims-console`,
   writes `deploy/.env`, builds the image (the reviewer image at the repository root: console,
   instrument code and the recorded sweeps, about 1 GB of downloads), starts it behind Caddy and
   prints two checks: `401` without the token and `"public":true,"hosted":true` with it. Caddy
   fetches the certificate on the first request, which needs step 2 to have propagated.
5. Rebuild the page with the service's address and the same token, and push it:
   ```bash
   python static/build_static.py --repo /path/to/AIMS --runs runs_console --include <run ids> \
       --out /path/to/leongarcia2023.github.io/AIMS --password <page password> \
       --backend https://aims.leonmgarcia.com --token <token> \
       --runlocal-repo https://github.com/leongarcia2023/aims-console --runlocal-image ghcr.io/leongarcia2023/aims-console
   cd /path/to/leongarcia2023.github.io && git add AIMS && git commit -m "AIMS console page: live service" && git push
   ```
6. Open `leonmgarcia.com/AIMS`, enter the page password: the note under the title says runs use the
   authors' key, the model list is live, the key field is gone. Edit the prompt, press Start run.
7. After the review: delete the VPS, remove the DNS record, revoke the key. The page falls back to
   replaying the recorded runs by itself; rebuild it without `--backend` to drop the address.

## What the service needs (for anyone redoing this elsewhere)

* Linux with Docker, two cores and 8 GB. The particle filter takes about a minute of one core
  per position estimate. GP regression is the heavy step: its simulator peaks at about 5.4 GB and
  it takes 5 to 9 minutes on two cores. The per-run simulators take turns at it through one lock
  file under the runs directory, so memory has to carry one regression plus what each run holds
  anyway (an idle simulator 0.4 GB, its agent process 0.2 to 0.4 GB), and a second run that needs
  a regression waits for the first. The compose file caps the container at `AIMS_CPUS` and
  `AIMS_MEMORY` (defaults 2 and 7g) and runs two loops at once (`AIMS_MAX_CONCURRENT`, default 2);
  raise the concurrency only with the memory.
  An earlier version of this file said 4 GB and a 3g cap. That was wrong for any run that reaches
  step 7.
* A host name whose A record points at it and ports 80 and 443 open, so Caddy can get a
  certificate. The page is served over HTTPS, and browsers refuse a plain-http API from an https page.
* The access token: the service accepts API calls only with it; the static page carries it inside
  its encrypted body, so the token is known to whoever has the page's password and to nobody else.
  Without the token the service is a closed door.
* Hosted mode: the Anthropic key in `/srv/anthropic.key` (mode 600). Byok mode: nothing more.

Changing the host name or the token means rebuilding and re-pushing the page (step 5).

## What the service does and does not do

* Every run starts its own simulated MCP server on a loopback port under the run's directory
  (`<run>/sim/sessions`), seeded differently, and stops it when the run ends. Runs never share an
  instrument state.
* Hosted mode: a Claude run with no key in the request uses the service's key; a request that does
  bring a key uses that one instead. Byok mode: a run without a key gets a 400 and nothing else. In
  both modes a key from the page goes into the run's process environment (Claude driver) or the
  request headers of the run's own calls (OpenAI driver); it is not written to `meta.json`,
  `events.jsonl` or the service log, and `meta.json` records only `own_key: true`.
* Caps: turns (`--max-turns-cap`, 80), wall time (`--wall-limit-min`, 45; the watchdog stops the
  run and marks it), concurrent runs (`--max-concurrent`, 2; the next gets a 429 and a message to
  try again later). In hosted mode these caps are the spend control per run; the Anthropic Console
  limit is the spend control overall.
* A run is visible only to the browser that started it (a random client id in that browser's
  local storage); the runs list and the WebSocket are filtered by it.
* The discovery loop is not offered: it needs file and shell tools on the server.
* CORS: only the origins in `--origins` may call the API from a page. The access token is the
  actual lock.
* Every run's log and simulator session stays under `/runs` (the `runs` volume). Prune now and
  then: `docker compose exec console find /runs -maxdepth 1 -mtime +30 -type d -exec rm -rf {} +`.
* The microscope PC is never the host: the hosted service runs simulators only.

## Updating the service

```bash
ssh root@<ip> bash /srv/bootstrap.sh aims.leonmgarcia.com <token> hosted
```

(the bootstrap pulls the repository, rebuilds and restarts; `deploy/.env` is rewritten from its
arguments and the key file). Logs:
`docker compose -f /srv/aims-console/aims_console/deploy/docker-compose.yml --env-file /srv/aims-console/aims_console/deploy/.env logs -f`.

## Alternatives, for the record

A lab machine behind a Cloudflare Tunnel (free, no open ports, but reviewer traffic enters the lab
network and the machine must stay on); a National Compute workspace behind the same kind of tunnel
(free, but a pod restart ends the tunnel and the URL changes); the lab's k8s cluster with a public
ingress (bills whole GPU nodes at 3.50 USD per GPU-hour, so no). Without Docker:
`pip install -r requirements.txt`, then
`AIMS_ACCESS_TOKEN=<token> AIMS_HOSTED=1 ANTHROPIC_API_KEY=... python -m backend.app --repo /srv/aims-console/aims --examples /srv/aims-console/aims/examples --runs /srv/aims-runs --sim-per-run --public --host 127.0.0.1 --port 8080 --origins https://leonmgarcia.com`
behind any HTTPS reverse proxy (Caddy: `aims.leonmgarcia.com { reverse_proxy 127.0.0.1:8080 }`).
