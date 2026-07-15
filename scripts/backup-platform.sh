#!/usr/bin/env bash
# Create a quiesced, encrypted CEI Labs platform backup.
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOYMENT_ROOT="${DEPLOYMENT_ROOT:-$REPO_ROOT}"
STACK_NAME="${STACK_NAME:-cei-labs}"
DEST_ROOT="${1:-$REPO_ROOT/backups}"
KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$DEST_ROOT/$RUN_ID"
CTFD_SERVICE="${STACK_NAME}_ctfd"
DB_SERVICE="${STACK_NAME}_ctfd-db"
ORCH_SERVICE="${STACK_NAME}_orchestrator"

for cmd in docker tar openssl sha256sum python3; do
  command -v "$cmd" >/dev/null || { echo "missing dependency: $cmd" >&2; exit 1; }
done
[[ -f "$KEY_FILE" ]] || { echo "BACKUP_ENCRYPTION_KEY_FILE must name a protected passphrase file" >&2; exit 1; }
key_mode="$(stat -c '%a' "$KEY_FILE")"
[[ "$key_mode" == "400" || "$key_mode" == "600" ]] || {
  echo "encryption key file must have mode 400 or 600 (found $key_mode)" >&2
  exit 1
}
docker info >/dev/null
docker service inspect "$CTFD_SERVICE" "$DB_SERVICE" "$ORCH_SERVICE" >/dev/null
[[ -f "$DEPLOYMENT_ROOT/docker/.env" ]] || {
  echo "deployment config missing: $DEPLOYMENT_ROOT/docker/.env" >&2
  exit 1
}
[[ -d "$DEPLOYMENT_ROOT/docker/secrets" ]] || {
  echo "deployment secrets directory missing: $DEPLOYMENT_ROOT/docker/secrets" >&2
  exit 1
}

mkdir -p "$DEST"
chmod 700 "$DEST"

container_for() {
  docker ps --filter "label=com.docker.swarm.service.name=$1" --format '{{.ID}}' | head -n 1
}

ctfd_image="$(docker service inspect "$CTFD_SERVICE" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
orch_image="$(docker service inspect "$ORCH_SERVICE" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
ctfd_replicas="$(docker service inspect "$CTFD_SERVICE" --format '{{.Spec.Mode.Replicated.Replicas}}')"
orch_replicas="$(docker service inspect "$ORCH_SERVICE" --format '{{.Spec.Mode.Replicated.Replicas}}')"

resume_services() {
  docker service scale "$CTFD_SERVICE=$ctfd_replicas" "$ORCH_SERVICE=$orch_replicas" >/dev/null 2>&1 || true
}
trap resume_services EXIT

echo "quiescing CTFd and orchestrator"
docker service scale "$CTFD_SERVICE=0" "$ORCH_SERVICE=0" >/dev/null
for _ in $(seq 1 60); do
  ctfd_running="$(docker service ls --filter "name=$CTFD_SERVICE" --format '{{.Replicas}}')"
  orch_running="$(docker service ls --filter "name=$ORCH_SERVICE" --format '{{.Replicas}}')"
  [[ "$ctfd_running" == 0/* && "$orch_running" == 0/* ]] && break
  sleep 1
done
[[ "$ctfd_running" == 0/* && "$orch_running" == 0/* ]] || { echo "services did not quiesce" >&2; exit 1; }

db_container="$(container_for "$DB_SERVICE")"
[[ -n "$db_container" ]] || { echo "CTFd database container is not running" >&2; exit 1; }
docker exec "$db_container" sh -c \
  'MYSQL_PWD="$(cat /run/secrets/ctfd_db_root_password)" exec mariadb-dump --user=root --single-transaction --routines --events --triggers ctfd' \
  > "$DEST/ctfd.sql"
[[ -s "$DEST/ctfd.sql" ]] || { echo "database dump is empty" >&2; exit 1; }

docker run --rm --entrypoint tar \
  -v "${STACK_NAME}_ctfd_uploads:/source:ro" "$ctfd_image" -C /source -cf - . \
  > "$DEST/ctfd-uploads.tar"
docker run --rm --entrypoint tar \
  -v "${STACK_NAME}_orchestrator_data:/source:ro" "$orch_image" -C /source -cf - . \
  > "$DEST/orchestrator-data.tar"

tar -C "$DEPLOYMENT_ROOT" -cf - \
  docker/.env docker/secrets docker/traefik/dynamic docker/traefik/certs \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$KEY_FILE" \
      -out "$DEST/protected-config.tar.enc"

git -C "$REPO_ROOT" rev-parse HEAD > "$DEST/engine-commit.txt"
if [[ -n "${WARGAMES_REPO:-}" && -d "$WARGAMES_REPO/.git" ]]; then
  git -C "$WARGAMES_REPO" rev-parse HEAD > "$DEST/wargames-commit.txt"
fi
docker version > "$DEST/docker-version.txt"
docker info > "$DEST/docker-info.txt"
docker node inspect self > "$DEST/swarm-node.json"
docker service inspect $(docker stack services "$STACK_NAME" -q) > "$DEST/services.json"
(
  set -a
  # shellcheck disable=SC1091
  source "$DEPLOYMENT_ROOT/docker/.env"
  set +a
  cd "$DEPLOYMENT_ROOT/docker"
  docker stack config -c "$REPO_ROOT/docker/stack.yml"
) > "$DEST/resolved-stack.yml"

BACKUP_RUN_ID="$RUN_ID" BACKUP_DIR="$DEST" python3 - <<'PY'
import json, os, socket
from datetime import datetime, timezone
from pathlib import Path

dest = Path(os.environ["BACKUP_DIR"])
manifest = {
    "format": "cei-labs-platform-backup-v1",
    "run_id": os.environ["BACKUP_RUN_ID"],
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "hostname": socket.gethostname(),
    "active_sessions_policy": "preserved for routine restart; clean-cluster restore requires participant relaunch verification",
}
(dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY

(
  cd "$DEST"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\0' \
    | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)

trap - EXIT
resume_services
echo "backup complete: $DEST"
