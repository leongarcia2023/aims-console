# AIMS console in public (bring-your-own-key) mode: per-run simulated instruments, no server
# credentials, caps on turns, wall time and concurrency. Build from the aims_console directory:
#
#     docker build -t aims-console -f deploy/Dockerfile .
#
# Run with the AIMS repository and the team's examples (VbgScans, GridSpectra, SampleLocatingImages)
# mounted read-only and a volume for the run logs:
#
#     docker run -d --name aims-console -p 127.0.0.1:8080:8080 -e AIMS_ACCESS_TOKEN=... \
#         -v /srv/AIMS:/aims:ro -v /srv/aims-examples:/examples:ro -v aims-runs:/runs \
#         aims-console --origins https://leonmgarcia.com
#
# deploy/docker-compose.yml does the same with Caddy in front for HTTPS; see deploy/DEPLOY.md.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /console
COPY requirements.txt /console/requirements.txt
RUN pip install --no-cache-dir -r /console/requirements.txt

COPY backend /console/backend
COPY sim /console/sim
COPY skills /console/skills
COPY web /console/web
COPY static /console/static
COPY README.md MANUAL.md /console/

# an unprivileged user; the Claude driver gives every run its own HOME under the run directory
RUN useradd --create-home --uid 1000 console \
 && mkdir -p /runs /aims /examples \
 && chown -R console:console /console /runs
USER console
ENV PYTHONUNBUFFERED=1 HOME=/home/console

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/config', timeout=8).status == 200 else 1)"

ENTRYPOINT ["python", "-m", "backend.app", "--repo", "/aims", "--examples", "/examples", "--runs", "/runs", \
            "--sim-per-run", "--public", "--host", "0.0.0.0", "--port", "8080"]
# anything after the image name is appended: --origins, --max-concurrent, --max-turns-cap, --wall-limit-min, --p-defect
CMD ["--origins", "https://leonmgarcia.com"]
