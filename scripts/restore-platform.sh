#!/usr/bin/env bash
# Restore a CEI Labs platform backup onto an empty Docker Swarm station.
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOYMENT_ROOT="${DEPLOYMENT_ROOT:-$REPO_ROOT}"
STACK_NAME="${STACK_NAME:-cei-labs}"
KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-}"
BACKUP_DIR=""
ASSUME_YES=false
START_EPOCH="$(date +%s)"
STAGE=""
DB_CONTAINER=""
CREATED_VOLUMES=()

usage() {
  echo "usage: BACKUP_ENCRYPTION_KEY_FILE=/protected/key $0 [--yes] <backup-directory>" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage; exit 1 ;;
    *) [[ -z "$BACKUP_DIR" ]] || { usage; exit 1; }; BACKUP_DIR="$1"; shift ;;
  esac
done

[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || { usage; exit 1; }
[[ -f "$KEY_FILE" ]] || { echo "BACKUP_ENCRYPTION_KEY_FILE is required" >&2; exit 1; }

for cmd in docker openssl tar python3 sha256sum; do
  command -v "$cmd" >/dev/null || { echo "missing dependency: $cmd" >&2; exit 1; }
done
docker info >/dev/null
[[ "$(docker info --format '{{.Swarm.LocalNodeState}}')" == active ]] || {
  echo "target must already be an active Docker Swarm" >&2
  exit 1
}

cleanup() {
  [[ -z "$DB_CONTAINER" ]] || docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
  [[ -z "$STAGE" ]] || rm -rf -- "$STAGE"
}
trap cleanup EXIT

echo "verifying backup before mutation"
BACKUP_ENCRYPTION_KEY_FILE="$KEY_FILE" "$REPO_ROOT/scripts/verify-backup.sh" "$BACKUP_DIR"

mapfile -t existing_services < <(docker stack services "$STACK_NAME" -q 2>/dev/null || true)
[[ ${#existing_services[@]} -eq 0 ]] || {
  echo "refusing to restore over existing stack: $STACK_NAME" >&2
  exit 1
}
for volume in ctfd_db_data ctfd_uploads orchestrator_data; do
  if docker volume inspect "${STACK_NAME}_${volume}" >/dev/null 2>&1; then
    echo "refusing to restore over existing volume: ${STACK_NAME}_${volume}" >&2
    exit 1
  fi
done

if [[ "$ASSUME_YES" != true ]]; then
  echo "Target: $(hostname); stack: $STACK_NAME; backup: $BACKUP_DIR"
  read -r -p "Type RESTORE ${STACK_NAME} to confirm this empty-station restore: " confirmation
  [[ "$confirmation" == "RESTORE ${STACK_NAME}" ]] || { echo "restore cancelled" >&2; exit 1; }
fi

STAGE="$(mktemp -d)"
chmod 700 "$STAGE"
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$KEY_FILE" \
  -in "$BACKUP_DIR/protected-config.tar.enc" -out "$STAGE/protected-config.tar"

python3 - "$STAGE/protected-config.tar" <<'PY'
import sys, tarfile
allowed_files = {"docker/.env"}
allowed_prefixes = ("docker/secrets/", "docker/traefik/dynamic/", "docker/traefik/certs/")
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        name = member.name.removeprefix("./")
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(f"unsafe protected-config path: {member.name}")
        if not (name in allowed_files or name.rstrip("/") in {p.rstrip("/") for p in allowed_prefixes}
                or name.startswith(allowed_prefixes)):
            raise SystemExit(f"unexpected protected-config path: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"links are not allowed in protected config: {member.name}")
PY
tar -xf "$STAGE/protected-config.tar" -C "$STAGE"
mkdir -p "$DEPLOYMENT_ROOT/docker"
cp -a "$STAGE/docker/." "$DEPLOYMENT_ROOT/docker/"

read_service() {
  python3 - "$BACKUP_DIR/services.json" "$STACK_NAME" "$1" "$2" <<'PY'
import json, sys
services, stack, suffix, field = json.load(open(sys.argv[1], encoding="utf-8-sig")), sys.argv[2], sys.argv[3], sys.argv[4]
name = f"{stack}_{suffix}"
matches = [s for s in services if s.get("Spec", {}).get("Name", "").endswith(f"_{suffix}")]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one service spec for {name}")
s = matches[0]
if field == "image": print(s["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"])
PY
}

db_image="$(read_service ctfd-db image)"
ctfd_image="$(read_service ctfd image)"
orch_image="$(read_service orchestrator image)"
db_root_secret="$DEPLOYMENT_ROOT/docker/secrets/ctfd_db_root_password.txt"
db_user_secret="$DEPLOYMENT_ROOT/docker/secrets/ctfd_db_password.txt"
[[ -s "$db_root_secret" && -s "$db_user_secret" ]] || { echo "restored database secrets are missing" >&2; exit 1; }

for volume in ctfd_db_data ctfd_uploads orchestrator_data; do
  docker volume create "${STACK_NAME}_${volume}" >/dev/null
  # Recorded so the readiness failure below can clean up exactly what this
  # run created. The guard near the top of the script proved none of these
  # pre-existed, so removing them cannot destroy anything the operator had.
  CREATED_VOLUMES+=("${STACK_NAME}_${volume}")
done

echo "restoring uploads and orchestrator data"
docker run --rm --entrypoint tar -i -v "${STACK_NAME}_ctfd_uploads:/restore" "$ctfd_image" \
  --touch -C /restore -xf - < "$BACKUP_DIR/ctfd-uploads.tar"
docker run --rm --entrypoint tar -i -v "${STACK_NAME}_orchestrator_data:/restore" "$orch_image" \
  --touch -C /restore -xf - < "$BACKUP_DIR/orchestrator-data.tar"

echo "initializing MariaDB from ctfd.sql"
DB_CONTAINER="${STACK_NAME}-restore-db-$$"
DB_VOLUME="${STACK_NAME}_ctfd_db_data"
docker run -d --name "$DB_CONTAINER" \
  -e MYSQL_DATABASE=ctfd -e MYSQL_USER=ctfd \
  -e MYSQL_ROOT_PASSWORD_FILE=/run/restore/root -e MYSQL_PASSWORD_FILE=/run/restore/user \
  -v "$DB_VOLUME:/var/lib/mysql" \
  -v "$db_root_secret:/run/restore/root:ro" -v "$db_user_secret:/run/restore/user:ro" \
  -v "$BACKUP_DIR/ctfd.sql:/docker-entrypoint-initdb.d/ctfd.sql:ro" "$db_image" >/dev/null

# The wait loop below used to have no failure branch. It polled
# `mariadb-admin ping` for up to 180s and, if the container stayed Running
# but never answered, the `for` simply fell through and execution continued
# into the real import — surfacing as a bare connection error with nothing
# between "MariaDB should be ready by now" and "it wasn't". Worse, the
# `docker volume create` above had already materialised cei-labs_ctfd_db_data
# with a partially initialized datadir, and the "refusing to restore over
# existing volume" guard near the top of this script then rejected the
# operator's retry — so one transient MariaDB failure became a manual
# `docker volume rm` with nothing in the output explaining why.
#
# `db_ready` is set explicitly instead of being inferred from a bare
# `break`, so "never became ready" is distinguishable from "became ready".
# An exited container also breaks out to this same branch rather than
# exiting from inside the loop, so the volume cleanup below always runs.
db_ready=false
db_exited=false
for _ in $(seq 1 180); do
  if docker exec "$DB_CONTAINER" sh -c \
    'MYSQL_PWD="$(cat /run/restore/root)" mariadb-admin --user=root ping --silent' >/dev/null 2>&1; then
    db_ready=true
    break
  fi
  if [[ "$(docker inspect "$DB_CONTAINER" --format '{{.State.Running}}')" != true ]]; then
    db_exited=true
    break
  fi
  sleep 1
done

if [[ "$db_ready" != true ]]; then
  if [[ "$db_exited" == true ]]; then
    echo "MariaDB container ${DB_CONTAINER} exited before accepting connections." >&2
  else
    echo "MariaDB in ${DB_CONTAINER} was still running but never accepted connections after 180s." >&2
  fi
  echo "Container logs (usual cause: ctfd.sql failing to import, or a corrupt/partial backup):" >&2
  docker logs "$DB_CONTAINER" >&2 || true
  # The container must go before the volumes: `docker volume rm` fails while
  # any container still references one, and failing here would leave
  # precisely the state this branch exists to clear.
  docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
  DB_CONTAINER=""
  # Drop every volume this run created, not just the database one. They are
  # all created in the same loop immediately above and are all rejected by
  # the "refusing to restore over existing volume" guard on the next
  # attempt, so cleaning up only ctfd_db_data would just make the retry fail
  # on cei-labs_ctfd_uploads instead and leave the operator exactly as stuck
  # as before. None of them pre-existed (checked at the top of this script)
  # and none holds anything but this failed run's partial import.
  for vol in "${CREATED_VOLUMES[@]}"; do
    if ! docker volume rm "$vol" >/dev/null 2>&1; then
      echo "WARNING: could not remove the partially initialized volume ${vol}." >&2
      echo "         A retry stays blocked by 'refusing to restore over existing volume' until you run:" >&2
      echo "           docker volume rm ${vol}" >&2
    fi
  done
  exit 1
fi

docker exec "$DB_CONTAINER" sh -c \
  'MYSQL_PWD="$(cat /run/restore/root)" mariadb --user=root --batch --skip-column-names -e "SELECT COUNT(*) FROM ctfd.users"' >/dev/null
docker rm -f "$DB_CONTAINER" >/dev/null
DB_CONTAINER=""

echo "deploying recorded resolved stack"
python3 - "$BACKUP_DIR/resolved-stack.yml" "$STAGE/deploy-stack.yml" <<'PY'
import sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
# `docker stack config` emits resolved literal dollars, while a later
# `docker stack deploy` parses them as interpolation again. Re-escape them so
# regex anchors and dollar-containing resolved values survive redeployment.
target.write_text(source.read_text(encoding="utf-8-sig").replace("$", "$$"), encoding="utf-8")
PY
(cd "$DEPLOYMENT_ROOT/docker" && docker stack deploy --with-registry-auth \
  -c "$STAGE/deploy-stack.yml" "$STACK_NAME")

echo "verifying deployed services against services.json"
python3 - "$BACKUP_DIR/services.json" "$STACK_NAME" <<'PY'
import json, subprocess, sys, time
expected_list, stack = json.load(open(sys.argv[1], encoding="utf-8-sig")), sys.argv[2]
expected = {}
for service in expected_list:
    recorded_name = service["Spec"]["Name"]
    suffix = recorded_name.split("_", 1)[1]
    expected[f"{stack}_{suffix}"] = service
deadline = time.monotonic() + 600
last = []
while time.monotonic() < deadline:
    ids = subprocess.check_output(["docker", "stack", "services", stack, "-q"], text=True).split()
    actual_list = json.loads(subprocess.check_output(["docker", "service", "inspect", *ids], text=True)) if ids else []
    actual = {s["Spec"]["Name"]: s for s in actual_list}
    errors = []
    if set(actual) != set(expected): errors.append(f"service set expected={sorted(expected)} actual={sorted(actual)}")
    for name in sorted(set(actual) & set(expected)):
        e, a = expected[name], actual[name]
        ei = e["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"]
        ai = a["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"]
        if ai != ei: errors.append(f"{name}: image expected={ei} actual={ai}")
        er = e["Spec"].get("Mode", {}).get("Replicated", {}).get("Replicas")
        ar = a["Spec"].get("Mode", {}).get("Replicated", {}).get("Replicas")
        if ar != er: errors.append(f"{name}: replicas expected={er} actual={ar}")
        if er is not None:
            running = subprocess.check_output(
                ["docker", "service", "ps", "--filter", "desired-state=running", "--format", "{{.CurrentState}}", name], text=True
            ).splitlines()
            if len(running) != er or any(not state.startswith("Running ") for state in running):
                errors.append(f"{name}: running replicas {len([x for x in running if x.startswith('Running ')])}/{er}")
    if not errors:
        print(f"services verification passed: {len(actual)} services")
        break
    last = errors
    time.sleep(3)
else:
    raise SystemExit("service verification timed out:\n  " + "\n  ".join(last))
PY

elapsed="$(( $(date +%s) - START_EPOCH ))"
echo "restore complete: ${elapsed}s"
trap - EXIT
cleanup
