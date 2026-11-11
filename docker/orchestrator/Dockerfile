FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
# Runs as root (the image default) because it needs to read
# /var/run/docker.sock, which is only accessible to root/the docker group on
# the host — see the security note on the `orchestrator` service in
# docker/stack.yml.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Must stay at exactly 1 worker: InstanceStore/RangeStore/reaper state is an
# in-memory Python object with no shared backing (no Redis/file/IPC). With
# >1 gunicorn worker, each is a separate OS process with its own copy of that
# state — a POST /instances handled by worker A is invisible to a later
# GET/DELETE routed to worker B, causing spurious 404s and orphaned
# containers. Verified: with --workers 2, DELETE on an instance created
# moments earlier 404s intermittently depending on which worker gets it,
# while GET against the same instance can still succeed.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "app.wsgi:app"]
