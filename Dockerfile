# AIMS console for reviewers: the console, the AIMS instrument code and the recorded example
# sweeps in one image. Runs the simulator; you bring your own API key in the page.
#
#   docker run --rm -p 8080:8080 ghcr.io/leongarcia2023/aims-console
#   open http://localhost:8080
#
# Built by .github/workflows/image.yml on every push to main; or locally: docker build -t aims-console .
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /console
COPY aims_console/requirements.txt /console/requirements.txt
RUN pip install --no-cache-dir -r /console/requirements.txt

COPY aims_console/backend /console/backend
COPY aims_console/sim /console/sim
COPY aims_console/skills /console/skills
COPY aims_console/web /console/web
COPY aims_console/static /console/static
COPY aims_console/README.md aims_console/MANUAL.md /console/
COPY aims /aims

RUN useradd --create-home --uid 1000 console \
 && mkdir -p /runs \
 && chown -R console:console /console /runs
USER console
ENV PYTHONUNBUFFERED=1 HOME=/home/console

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/template', timeout=8).status == 200 else 1)"

# public mode: bring your own key, one simulator per run, caps; no credentials in the image.
# --origins is irrelevant on localhost but harmless; append flags after the image name to change caps.
ENTRYPOINT ["python", "-m", "backend.app", "--repo", "/aims", "--examples", "/aims/examples", "--runs", "/runs", \
            "--sim-per-run", "--public", "--host", "0.0.0.0", "--port", "8080"]
CMD ["--max-concurrent", "2"]
