#!/usr/bin/env bash
# scripts/stack-up.sh
# Deploys the full CEI Labs stack to Docker Swarm — replaces platform-up.sh
# (which drove kubectl/helm against a K3s cluster).
#
# Run this after ansible/site.yml has provisioned hosts and formed the swarm,
# or directly on a single machine that already has Docker installed (it will
# initialize a one-node swarm itself if needed).
#
# Usage:
#     ./scripts/stack-up.sh
#     ./scripts/stack-up.sh --dry-run

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"
STACK_NAME="cei-labs"
DRY_RUN=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    *) log_error "Unknown argument: $1"; echo "Usage: $0 [--dry-run]"; exit 1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] $*"
  else
    log_info "$*"
    "$@"
  fi
}

# ── Preflight: dependencies ───────────────────────────────────────────────────
verify_dependencies() {
  local missing=()
  for cmd in docker curl jq; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Missing required dependencies: ${missing[*]}"
    exit 1
  fi
  if ! docker compose version &>/dev/null; then
    log_error "Docker Compose plugin not found (needed for stack file syntax). Install Docker Engine 24+."
    exit 1
  fi
}

# ── Preflight: config files present and filled in ────────────────────────────
verify_config() {
  if [[ ! -f "$DOCKER_DIR/.env" ]]; then
    log_error "docker/.env not found."
    log_error "Run: cp docker/.env.example docker/.env   and fill in every value."
    exit 1
  fi

  if [[ ! -d "$DOCKER_DIR/secrets" ]]; then
    log_error "docker/secrets/ not found."
    log_error "Run: cp -r docker/secrets.example docker/secrets   and replace every CHANGE_ME value."
    exit 1
  fi

  local placeholder_found=false
  for example in "$DOCKER_DIR"/secrets.example/*.txt; do
    local name; name="$(basename "$example")"
    local real="$DOCKER_DIR/secrets/$name"
    if [[ ! -f "$real" ]]; then
      log_error "Missing docker/secrets/$name (see docker/secrets.example/$name)"
      placeholder_found=true
      continue
    fi
    if grep -q "CHANGE_ME" "$real"; then
      log_warn "docker/secrets/$name still contains a CHANGE_ME placeholder — replace it before a real event."
      placeholder_found=true
    fi
  done
  if [[ "$placeholder_found" == "true" && "$DRY_RUN" == "false" ]]; then
    read -rp "Continue anyway? [y/N] " CONTINUE
    [[ "${CONTINUE,,}" == "y" ]] || exit 1
  fi
}

# ── Preflight: swarm state ────────────────────────────────────────────────────
verify_swarm() {
  local swarm_state
  swarm_state=$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo "inactive")
  if [[ "$swarm_state" != "active" ]]; then
    log_warn "This host is not part of a Docker Swarm yet."
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY-RUN] docker swarm init"
    else
      run docker swarm init
    fi
  else
    log_info "Swarm already active on this host."
  fi

  # Ensure at least one node is labeled to host stateful services. On a
  # freshly-initialized single-node swarm this is just "myself"; on a swarm
  # ansible/site.yml already built, the primary manager is already labeled.
  if [[ "$DRY_RUN" == "false" ]]; then
    local self_id already_labeled
    self_id=$(docker node inspect self --format '{{.ID}}' 2>/dev/null || echo "")
    if [[ -n "$self_id" ]]; then
      already_labeled=$(docker node inspect self --format '{{index .Spec.Labels "ctfd-data"}}' 2>/dev/null || echo "")
      if [[ "$already_labeled" != "true" ]]; then
        local any_labeled
        any_labeled=$(docker node ls --filter "label=ctfd-data=true" -q 2>/dev/null || echo "")
        if [[ -z "$any_labeled" ]]; then
          log_info "No node labeled ctfd-data=true yet — labeling this manager."
          docker node update --label-add ctfd-data=true "$self_id" >/dev/null
        fi
      fi
    fi
  fi
}

verify_dependencies
verify_config
verify_swarm

echo "═══════════════════════════════════════════════════"
echo "  CEI Labs Engine — Docker Swarm Deployment"
echo "═══════════════════════════════════════════════════"

# `docker stack deploy` resolves relative bind-mount paths (traefik/dynamic,
# traefik/certs, secrets/*.txt) relative to the current working directory, so
# run it from inside docker/ rather than passing -c docker/stack.yml from the
# repo root.
cd "$DOCKER_DIR"
run docker stack deploy --with-registry-auth -c stack.yml "$STACK_NAME"

if [[ "$DRY_RUN" == "false" ]]; then
  log_info "Waiting for services to converge..."
  for _ in $(seq 1 30); do
    NOT_READY=$(docker stack services "$STACK_NAME" --format '{{.Name}} {{.Replicas}}' | awk -F'[ /]' '$2 != $3 {print $1}')
    [[ -z "$NOT_READY" ]] && break
    sleep 5
  done
  if [[ -n "${NOT_READY:-}" ]]; then
    log_warn "Still converging: ${NOT_READY}. Check with: docker stack services ${STACK_NAME}"
  else
    log_info "All services converged."
  fi
fi

# shellcheck disable=SC1091
set -a; source "$DOCKER_DIR/.env"; set +a

echo ""
echo "═══════════════════════════════════════════════════"
log_info "Deployment complete."
echo ""
echo "  Access points (add to /etc/hosts or local DNS if using .local domains):"
echo "    https://ctfd.${BASE_DOMAIN:-ctf.local}   → CTFd scoring platform"
echo ""
echo "  Any node's IP works — Swarm's routing mesh forwards to wherever"
echo "  Traefik actually landed."
echo ""
echo "  Next steps:"
echo "    1. Open https://ctfd.${BASE_DOMAIN:-ctf.local} and complete the CTFd setup wizard"
echo "    2. Generate an admin API token, then: export CTFD_ADMIN_TOKEN=..."
echo "    3. Load challenges:   ./scripts/challenges-load.sh"
echo "    4. Bulk-spawn workspaces (optional): ./scripts/spawn-workspaces.sh roster.txt"
echo "    5. Check platform status: ./scripts/status.sh"
echo "═══════════════════════════════════════════════════"
