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

print_header() {
  echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗"
  echo -e "║      CEI Labs Engine — Docker Swarm Status                ║"
  echo -e "║                  $(date '+%Y-%m-%d %H:%M:%S')                     ║"
  echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

show_nodes() {
  echo -e "${BLUE}=== 1. Swarm Nodes ===${NC}"
  docker node ls
  echo ""
}

show_services() {
  echo -e "${BLUE}=== 2. Stack Services (${STACK_NAME}) ===${NC}"
  if ! docker stack services "$STACK_NAME" 2>/dev/null; then
    echo -e "  ${YELLOW}Stack '${STACK_NAME}' is not deployed. Run ./scripts/stack-up.sh${NC}"
  fi
  echo ""
}

show_service_health() {
  echo -e "${BLUE}=== 3. Service Task Health ===${NC}"
  local unhealthy
  unhealthy=$(docker stack ps "$STACK_NAME" --filter "desired-state=running" --format '{{.CurrentState}} {{.Name}}' 2>/dev/null | grep -viE '^running|^preparing|^starting|^assigned' || true)
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
  local svcs
  svcs=$(docker service ls --filter "label=app=workspace" --format '{{.Name}} {{.Replicas}}')
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
  svcs=$(docker service ls --filter "label=cei.orchestrator.managed=true" --format '{{.Name}} {{.Replicas}}')
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
  [[ -f "$REPO_ROOT/docker/.env" ]] && domain=$(grep -E '^BASE_DOMAIN=' "$REPO_ROOT/docker/.env" | cut -d= -f2- || echo "ctf.local")
  local status_code
  status_code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 3 "https://ctfd.${domain}" 2>/dev/null || echo "000")
  if [[ "$status_code" =~ ^(200|302)$ ]]; then
    echo -e "  ${GREEN}✓ https://ctfd.${domain} — HTTP ${status_code}${NC}"
  else
    echo -e "  ${YELLOW}! https://ctfd.${domain} — HTTP ${status_code} (may still be starting, or DNS/hosts not pointed here)${NC}"
  fi
  echo ""
}

print_header
show_nodes
show_services
show_service_health
show_workspaces
show_orchestrator_instances
show_reachability
