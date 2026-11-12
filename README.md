# AIMS console

A console for the three loops of AIMS (navigation, measurement, discovery) that runs the team's
own prompts against the instrument's MCP tools, or against a simulated instrument, and keeps a
complete log of every run. Companion to the paper; the page at
[leonmgarcia.com/AIMS](https://leonmgarcia.com/AIMS) replays recorded runs and explains it.

## Run it on your own machine

The console runs locally, on your own API key (Anthropic or OpenAI), against a simulated
microscope. The key goes into the page and stays on your computer; the authors run no service.

With Docker:

```bash
docker run --rm -p 8080:8080 ghcr.io/leongarcia2023/aims-console
```

Without Docker (git and Python 3.11 or 3.12):

```bash
curl -fsSL https://raw.githubusercontent.com/leongarcia2023/aims-console/main/install.sh | bash
```

Then open <http://localhost:8080>, pick a model, paste your key, start a navigation run.
`aims_console/MANUAL.md` is the user guide; `aims_console/README.md` the technical reference.

Memory: step 7 of the navigation loop (GP regression) takes 5 to 9 minutes and peaks at about
5.4 GB. Give Docker 7 GB, or have that much free without Docker; with less than 6 GB the
simulated instrument is killed at that step. The end of section 4 of the manual lists what the simulator does not
reproduce.

## What is in here

* `aims_console/`: the console (FastAPI service, two model drivers, simulator, page, skills).
* `aims/`: the parts of the [AIMS repository](https://github.com/leongarcia2023/aims) the
  console needs (`mcpServer/`, `prompts/`, `templates/`) and the recorded example sweeps and
  spectra the simulator replays (`examples/`). MIT licence, Philip David Suh and Siyuan Qiu.
* `Dockerfile`, `.github/workflows/image.yml`: the reviewer image and the build that publishes it.
* `install.sh`: the no-Docker path (clones this repository, creates a virtualenv, starts the console).
* `aims_console/deploy/nc_workspace.sh` and `NC_WORKSPACE.md`: the same console inside a National
  Compute Marshall workspace, for members of an organization that has one (the compute moves there;
  the model key is still yours).
* `rerun_kit/`: the scripts behind the blinded discovery-loop reruns (arm builders, the sandboxed
  runner, campaign driver, audits, token ledger, aggregation), with their own README.

The discovery loop needs file and shell tools and the discovery packet; it is not offered by the
local console. `rerun_kit/` runs it the way the paper's supplementary material describes.
