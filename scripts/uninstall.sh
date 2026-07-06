#!/usr/bin/env bash
# scripts/uninstall.sh - Interactive Uninstaller (Docker Swarm edition)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/uninstall.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

print_header() {
  clear
  echo -e "${RED}╔══════════════════════════════════════════════════════════╗"
  echo -e "║           CEI Labs Engine - Uninstaller                  ║"
  echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

verify_dependencies() {
  command -v docker &>/dev/null || { log_error "docker not found on PATH."; exit 1; }
}

select_depth() {
  print_header
  echo -e "${YELLOW}Select Destructive Scope & Uninstall Depth:${NC}"
  echo "1) Safe     - Remove bulk-spawned workspaces + self-service challenge instances. Keeps CTFd, its data, and the swarm intact. (Recommended)"
  echo "2) Full     - Also remove the CEI Labs stack itself (docker stack rm). Data volumes preserved."
  echo "3) Nuclear  - Full teardown + purge data volumes + leave the Docker Swarm on this node."
  echo ""
  read -rp "Choice [1-3] (default 1): " DEPTH
  DEPTH=${DEPTH:-1}
  case $DEPTH in
    1) DEPTH_LEVEL="safe" ;;
    2) DEPTH_LEVEL="full" ;;
    3) DEPTH_LEVEL="nuclear" ;;
    *) DEPTH_LEVEL="safe" ;;
  esac
}

remove_dynamic_workloads() {
  log_info "Removing bulk-spawned workspaces..."
  if [[ -x "$REPO_ROOT/scripts/spawn-workspaces.sh" ]]; then
    "$REPO_ROOT/scripts/spawn-workspaces.sh" --teardown --type analyst || true
    "$REPO_ROOT/scripts/spawn-workspaces.sh" --teardown --type kali || true
  fi

  log_info "Removing self-service challenge instances (orchestrator-managed services)..."
  local ids
  ids=$(docker service ls --filter "label=cei.orchestrator.managed=true" -q 2>/dev/null || true)
  [[ -n "$ids" ]] && echo "$ids" | xargs -r docker service rm
  local nets
  nets=$(docker network ls --filter "label=cei.orchestrator.managed=true" -q 2>/dev/null || true)
  [[ -n "$nets" ]] && echo "$nets" | xargs -r docker network rm
}

main() {
  print_header
  verify_dependencies
  log_warn "WARNING: You are about to initiate the uninstallation process for CEI Labs Engine."
  log_warn "Depending on the chosen depth, this action can permanently remove infrastructure and data."
  echo ""
  read -rp "Type 'DESTROY' to confirm uninstall: " CONFIRM
  [[ "$CONFIRM" != "DESTROY" ]] && { log_info "Teardown canceled by operator."; exit 0; }

  select_depth
  log_info "Uninstall execution started at depth: [${DEPTH_LEVEL^^}]"

  if [[ "$DEPTH_LEVEL" == "safe" ]]; then
    remove_dynamic_workloads
    log_info "Safe-mode cleanup finalized. CTFd and its data are untouched."
  fi

  if [[ "$DEPTH_LEVEL" == "full" ]]; then
    remove_dynamic_workloads
    if [[ -x "$REPO_ROOT/scripts/stack-down.sh" ]]; then
      "$REPO_ROOT/scripts/stack-down.sh"
    else
      docker stack rm cei-labs || true
    fi
  fi

  if [[ "$DEPTH_LEVEL" == "nuclear" ]]; then
    remove_dynamic_workloads
    if [[ -x "$REPO_ROOT/scripts/stack-down.sh" ]]; then
      "$REPO_ROOT/scripts/stack-down.sh" --purge-data
    else
      docker stack rm cei-labs || true
    fi

    read -rp "Leave the Docker Swarm on this node entirely? [y/N]: " LEAVE_SWARM
    if [[ "${LEAVE_SWARM,,}" == "y" ]]; then
      log_warn "Leaving Docker Swarm on this node..."
      docker swarm leave --force || true
    fi

    read -rp "Purge local forensic case storage at /opt/ctf-cases? [y/N]: " PURGE_CASES
    if [[ "${PURGE_CASES,,}" == "y" ]]; then
      log_error "Purging /opt/ctf-cases..."
      sudo rm -rf /opt/ctf-cases
    fi
  fi

  log_info "Uninstall processing lifecycle completed."
}

main "$@"
