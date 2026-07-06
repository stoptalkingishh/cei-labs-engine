FROM python:3.12-slim
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

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "app.wsgi:app"]
