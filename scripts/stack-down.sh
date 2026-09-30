#!/usr/bin/env bash
# scripts/stack-down.sh
# Tears down the CEI Labs Swarm stack — replaces platform-down.sh (which
# uninstalled Helm releases and deleted k8s manifests).
#
# Named volumes (ctfd_db_data, ctfd_uploads) are preserved by default, same
# as the old script's "Core persistence volumes preserved" behavior.
#
# Usage:
#   ./scripts/stack-down.sh              tear down the stack, keep data volumes
#   ./scripts/stack-down.sh --purge-data  also delete ctfd_db_data/ctfd_uploads

set -euo pipefail

STACK_NAME="cei-labs"
PURGE_DATA=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-data) PURGE_DATA=true; shift ;;
    *) echo "Usage: $0 [--purge-data]"; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log_warn "Evicting bulk-spawned analyst/kali workspaces..."
if [[ -x "$REPO_ROOT/scripts/spawn-workspaces.sh" ]]; then
  "$REPO_ROOT/scripts/spawn-workspaces.sh" --teardown --type analyst || true
  "$REPO_ROOT/scripts/spawn-workspaces.sh" --teardown --type kali || true
fi

# Self-service instances created by the orchestrator are NOT part of the
# stack and stay attached to challenge-edge/orchestrator-internal — `docker
# stack rm` cannot remove those networks while anything is still on them.
# Verified: leaving even one running instance makes stack rm partially fail
# with "network ... is in use by service ...".
log_warn "Removing self-service challenge instances (orchestrator-managed)..."
ids=$(docker service ls --filter "label=cei.orchestrator.managed=true" -q 2>/dev/null || true)
[[ -n "$ids" ]] && echo "$ids" | xargs -r docker service rm
nets=$(docker network ls --filter "label=cei.orchestrator.managed=true" -q 2>/dev/null || true)
[[ -n "$nets" ]] && echo "$nets" | xargs -r docker network rm

log_warn "Removing stack '${STACK_NAME}'..."
# `docker stack rm` exits 1 with "Nothing found in stack: cei-labs" when the
# stack is already gone. Under `set -e` that killed the script here, so the
# --purge-data volume removal below never ran and the operator never saw the
# "stopped" message — and uninstall.sh, which calls this unguarded, aborted
# mid-sequence and skipped its remaining confirmation prompts. Re-running
# teardown is normal, so treat absent-stack as success.
if ! docker stack rm "$STACK_NAME"; then
  log_info "Stack '${STACK_NAME}' is not deployed — nothing to remove."
fi

log_info "Waiting for services and networks to fully drain..."
for _ in $(seq 1 30); do
  remaining=$(docker service ls --filter "label=com.docker.stack.namespace=${STACK_NAME}" -q 2>/dev/null || true)
  [[ -z "$remaining" ]] && break
  sleep 2
done

if [[ "$PURGE_DATA" == "true" ]]; then
  log_warn "Purging persistent data volumes (ctfd_db_data, ctfd_uploads)..."
  # Verified per-volume rather than a single `rm` of both names: one volume
  # still in use (or simply absent) made the whole multi-name `rm` exit 1 and
  # `2>/dev/null || true` swallowed that with no trace, so --purge-data
  # reported success while the surviving volume's data was still on disk.
  for vol in "${STACK_NAME}_ctfd_db_data" "${STACK_NAME}_ctfd_uploads"; do
    if docker volume rm "$vol" 2>/dev/null; then
      log_info "Removed volume ${vol}."
    elif docker volume inspect "$vol" >/dev/null 2>&1; then
      # Still exists after a failed removal: the stack (or some other
      # container) is holding it. Naming the survivor is the whole point —
      # an operator who believes --purge-data worked will otherwise not look.
      log_warn "Volume ${vol} was NOT removed (still in use by a running container). Remove it manually once the stack is gone: docker volume rm ${vol}"
    else
      log_info "Volume ${vol} was already absent."
    fi
  done
else
  log_info "Persistent data volumes preserved (ctfd_db_data, ctfd_uploads). Use --purge-data to remove them."
fi

log_info "CEI Labs Engine stopped."
