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

log_warn "Removing stack '${STACK_NAME}'..."
docker stack rm "$STACK_NAME"

log_info "Waiting for services and networks to fully drain..."
for _ in $(seq 1 30); do
  remaining=$(docker service ls --filter "label=com.docker.stack.namespace=${STACK_NAME}" -q)
  [[ -z "$remaining" ]] && break
  sleep 2
done

if [[ "$PURGE_DATA" == "true" ]]; then
  log_warn "Purging persistent data volumes (ctfd_db_data, ctfd_uploads)..."
  docker volume rm "${STACK_NAME}_ctfd_db_data" "${STACK_NAME}_ctfd_uploads" 2>/dev/null || true
else
  log_info "Persistent data volumes preserved (ctfd_db_data, ctfd_uploads). Use --purge-data to remove them."
fi

log_info "CEI Labs Engine stopped."
