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

# `docker run -v <named-volume>:/path` CREATES the volume when it does not
# exist, so a typo'd STACK_NAME (or an orchestrator not yet redeployed under
# the current name) did not fail here — it silently produced a tar of an empty
# directory. Both archives below then passed every existing check: the
# checksum recorded them, and verify-backup.sh only asserted the file was
# non-empty and that tar could read it. The result was a "verified" backup
# containing zero uploads and zero orchestrator state, indistinguishable from
# a good one until the moment someone needed to restore it. Fail here instead.
for volume in "${STACK_NAME}_ctfd_uploads" "${STACK_NAME}_orchestrator_data"; do
  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "required volume '$volume' does not exist — refusing to back up an empty substitute" >&2
    echo "check STACK_NAME (currently '${STACK_NAME}') against the deployed stack" >&2
    exit 1
  fi
done

docker run --rm --entrypoint tar \
  -v "${STACK_NAME}_ctfd_uploads:/source:ro" "$ctfd_image" -C /source -cf - . \
  > "$DEST/ctfd-uploads.tar"
docker run --rm --entrypoint tar \
  -v "${STACK_NAME}_orchestrator_data:/source:ro" "$orch_image" -C /source -cf - . \
  > "$DEST/orchestrator-data.tar"

# A volume that exists can still be empty (created but never written to, e.g.
# by an interrupted first deploy), which tar renders as a valid archive of
# "./" entries. The orchestrator store is the one archive that must never be
# empty — a restore without it comes up with no instance registry at all — so
# assert real content rather than a readable tarball.
if [[ -z "$(tar -tf "$DEST/orchestrator-data.tar" | grep -v '/\?$' | head -n 1)" ]]; then
  echo "orchestrator-data.tar contains no files — the orchestrator volume is empty" >&2
  exit 1
fi

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
mapfile -t stack_service_ids < <(docker stack services "$STACK_NAME" -q)
docker service inspect "${stack_service_ids[@]}" > "$DEST/services.json"
(
  set -a
  # shellcheck disable=SC1091
  source "$DEPLOYMENT_ROOT/docker/.env"
  set +a
  cd "$DEPLOYMENT_ROOT/docker"
  # DEPLOYMENT_ROOT, not REPO_ROOT: every other path in this backup honours it
  # (the config tar, the .env source, the cd above), and it exists precisely to
  # support a deployment tree distinct from the engine checkout. Resolving the
  # stack from REPO_ROOT recorded the wrong stack whenever the two differed —
  # and this file is the sole input to the restore deploy, so the wrong stack
  # is what gets deployed.
  docker stack config -c "$DEPLOYMENT_ROOT/docker/stack.yml"
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
