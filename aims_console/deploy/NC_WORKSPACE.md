# Running the console in a National Compute workspace

For members of an organization on National Compute (the Shen laboratory has one, `shen-laboratory`).
Every member gets a Marshall workspace: a persistent Linux container (Debian 12, 1.5 CPU, 7 GiB RAM,
49 GB disk under `/data`, open egress to PyPI, GitHub, npm and `api.anthropic.com`) with shell panes
in the console. The console runs there the way it runs on a laptop, and the workspace serves it to
you through your console sign-in, so nothing is exposed to the public internet and no laptop has to
stay awake.

What this does and does not do, checked on 2026-11-09 in the Shen laboratory's organization:

* It moves the compute (the console service, the simulators, the agent subprocess) onto National
  Compute. The workspace is included in membership at every level, so this costs the organization
  nothing beyond its plan.
* It does not move the model billing. National Compute's own model usage ("Marshall model usage",
  a weekly allowance per member and then the organization's credits) is spent only by Marshall, the
  platform's agent; there is no API endpoint or credential that lets a program in a workspace call a
  model on the organization's account, and Marshall's model is not selectable or named. The console
  therefore still runs on an Anthropic or OpenAI key entered in the page, billed to that key's
  account. The workspace holds no key: the console runs in bring-your-own-key mode, the key lives in
  the run's process and in your browser's session storage.
* The discovery loop is not offered by the console in this mode (it needs file and shell tools on
  the agent's side and the discovery packet); `rerun_kit/` covers it and runs in the same workspace.

## Start it

In the console: organization, Marshall, Terminal (top right). In the pane:

```bash
curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/aims_console/deploy/nc_workspace.sh | bash
```

The script installs a Python 3.12 build for your user with `uv` (the workspace's system Python is
3.11; no sudo is available or needed), clones or updates the console under
`/data/workspace/aims-console-local`, and starts it in a tmux session called `aims` on port 8080. The
first start installs the requirements, which takes a few minutes on the workspace's CPU; the log is
`/data/workspace/aims-console.log`. When the log says the console is on `http://localhost:8080`, click
that link in the pane (or in a Marshall reply): the workspace opens it under the `/port/8080/` route of
your workspace address. The page addresses the service relative to that prefix, so the page, its
assets, the API and the run WebSocket all work there.

Pick a model, paste your key, start a navigation run. `tmux attach -t aims` watches the service
(Ctrl-B then D detaches), `tmux kill-session -t aims` stops it, and running the script again updates
and restarts it. The workspace disk survives restarts; a pod restart ends the tmux session, so run the
script again after one.

## Notes

* Runs, logs and the virtualenv live under `/data/workspace/aims-console-local/aims_console/`
  (`runs_local/`, `.venv/`). Nothing is written to the shared `/org` folder.
* The workspace shell has no grid credential, by design: it cannot spend the organization's credits.
* Docker is on the PATH in the workspace but had no reachable daemon when this was written, so the
  `docker run` path of the README does not apply there; the script above is the equivalent.
