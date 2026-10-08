#!/usr/bin/env bash
# scripts/status.sh
# Live snapshot of the CEI Labs Docker Swarm deployment — replaces the old
# kubectl/systemctl-based dashboard. The historical CSV trend view was
# dropped as ops-nicety, not core function; this is a single point-in-time
# view, re-run it whenever you want a fresh one.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="cei-labs"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'

# Every probe in this file is a diagnostic, not an assertion. This script is
# run precisely when something is broken, so a failing `docker ...` must
# report "cannot check X" and let the remaining sections print. Under
# `set -euo pipefail` a bare `docker node ls` on a non-manager host (or with
# the daemon down) exited 1 and killed the script at section 1 of 7, so the
# operator got one line of output and no clue about the other six. The
# asymmetry that caused it: section 2 already guarded with `if !` and
# sections 3/6/7 already redirected errors, but the first probe was bare.
cannot_check() {
  echo -e "  ${YELLOW}! Cannot check $1: $2${NC}"
}

# `docker stack services` is the cheapest reliable "is it deployed?" test
# and, unlike `docker stack ls`, is scoped to the one stack we care about.
stack_deployed() {
  [[ -n "$(docker stack services "$STACK_NAME" -q 2>/dev/null)" ]]
}

# Run one section; a section that dies anyway must not take the rest with it.
run_section() {
  local fn="$1"
  "$fn" || echo -e "  ${YELLOW}! Section ${fn#show_} could not complete (docker unavailable, or this host is not a swarm manager).${NC}"
}

print_header() {
  echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗"
  echo -e "║      CEI Labs Engine — Docker Swarm Status                ║"
  echo -e "║                  $(date '+%Y-%m-%d %H:%M:%S')                     ║"
  echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

show_nodes() {
  echo -e "${BLUE}=== 1. Swarm Nodes ===${NC}"
  # Non-fatal by construction: `if !` is what keeps `set -e` from aborting
  # here. The first two failure modes are "this host is a worker, not a
  # manager" and "the daemon is down" — both are expected on a station you
  # are trying to diagnose.
  if ! docker node ls; then
    cannot_check "swarm nodes" "this host is not a swarm manager, or the Docker daemon is unavailable."
  fi
  echo ""
}

show_services() {
  echo -e "${BLUE}=== 2. Stack Services (${STACK_NAME}) ===${NC}"
  if ! stack_deployed; then
    echo -e "  ${YELLOW}Stack '${STACK_NAME}' is not deployed. Run ./scripts/stack-up.sh${NC}"
  elif ! docker stack services "$STACK_NAME"; then
    cannot_check "stack services" "the daemon refused the query."
  fi
  echo ""
}

show_service_health() {
  echo -e "${BLUE}=== 3. Service Task Health ===${NC}"
  # Previously an unguarded pipeline whose `|| true` made "docker failed" and
  # "every task is healthy" indistinguishable — an undeployed stack reported
  # a green "✓ All tasks running", which is worse than printing nothing.
  if ! stack_deployed; then
    cannot_check "task health" "stack '${STACK_NAME}' is not deployed, so there are no tasks to inspect."
    echo ""
    return 0
  fi
  local running_tasks unhealthy
  if ! running_tasks=$(docker stack ps "$STACK_NAME" --filter "desired-state=running" \
      --format '{{.CurrentState}} {{.Name}}' 2>/dev/null); then
    cannot_check "task health" "the daemon refused 'docker stack ps'."
    echo ""
    return 0
  fi
  unhealthy=$(printf '%s\n' "$running_tasks" | grep -viE '^running|^preparing|^starting|^assigned' || true)
  if [[ -n "$unhealthy" ]]; then
    echo -e "  ${RED}Tasks not in a healthy state:${NC}"
    echo "$unhealthy" | sed 's/^/    /'
  else
    echo -e "  ${GREEN}✓ All tasks running${NC}"
  fi
  echo ""
}

show_workspaces() {
  echo -e "${BLUE}=== 4. Bulk-Spawned Workspaces ===${NC}"
  # A failing command substitution is itself fatal under `set -e`: this was
  # a second site that died outright when the daemon was down.
  local svcs
  if ! svcs=$(docker service ls --filter "label=app=workspace" --format '{{.Name}} {{.Replicas}}' 2>/dev/null); then
    cannot_check "bulk-spawned workspaces" "the Docker daemon is unavailable."
    echo ""
    return 0
  fi
  if [[ -z "$svcs" ]]; then
    echo -e "  ${GREEN}✓ None active.${NC}"
  else
    echo "$svcs" | sed 's/^/  /'
  fi
  echo ""
}

show_orchestrator_instances() {
  echo -e "${BLUE}=== 5. Self-Service Challenge Instances ===${NC}"
  local svcs
  if ! svcs=$(docker service ls --filter "label=cei.orchestrator.managed=true" --format '{{.Name}} {{.Replicas}}' 2>/dev/null); then
    cannot_check "self-service challenge instances" "the Docker daemon is unavailable."
    echo ""
    return 0
  fi
  if [[ -z "$svcs" ]]; then
    echo -e "  ${GREEN}✓ None active.${NC}"
  else
    echo "$svcs" | sed 's/^/  /'
  fi
  echo -e "  ${YELLOW}Admin dashboard: curl -H \"X-Admin-Auth: \$(cat docker/secrets/orchestrator_admin_password.txt)\" http://<manager>:8080/admin/instances (from inside the orchestrator-internal network)${NC}"
  echo ""
}

show_reachability() {
  echo -e "${BLUE}=== 6. CTFd Reachability ===${NC}"
  local domain="ctf.local"
  if [[ -f "$REPO_ROOT/docker/.env" ]]; then
    domain=$(grep -E '^BASE_DOMAIN=' "$REPO_ROOT/docker/.env" | cut -d= -f2- || echo "ctf.local")
  fi
  local status_code
  # `|| echo 000` only fires when curl printed nothing at all; if it printed a
  # code *and* failed, the old form appended a second line ("000000") that
  # matched neither branch. Assign first, then default.
  if ! status_code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 3 "https://ctfd.${domain}" 2>/dev/null); then
    status_code="000"
  fi
  if [[ "$status_code" =~ ^(200|302)$ ]]; then
    echo -e "  ${GREEN}✓ https://ctfd.${domain} — HTTP ${status_code}${NC}"
  else
    echo -e "  ${YELLOW}! https://ctfd.${domain} — HTTP ${status_code} (may still be starting, or DNS/hosts not pointed here)${NC}"
  fi
  echo ""
}

show_monitoring_tools() {
  echo -e "${BLUE}=== 7. Resource Monitoring ===${NC}"
  if command -v btop >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓ btop installed:${NC} tmux new -As cei-monitor btop"
  else
    echo -e "  ${YELLOW}! btop is not installed. Re-run the common Ansible role.${NC}"
  fi
  echo "  Evidence collector: ./scripts/capture-resources.sh [output-directory]"
  echo ""
}

print_header
run_section show_nodes
run_section show_services
run_section show_service_health
run_section show_workspaces
run_section show_orchestrator_instances
run_section show_reachability
run_section show_monitoring_tools
