#!/usr/bin/env bash
# scripts/status.sh - Advanced CEI Labs Monitoring Dashboard
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/status.log"
HISTORY_CSV="$REPO_ROOT/status-history.csv"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

print_header() {
    clear
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗"
    echo -e "║     CEI Labs Engine - Advanced System Monitoring         ║"
    echo -e "║                  $(date '+%Y-%m-%d %H:%M:%S')                      ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

init_history() {
    [[ ! -f "$HISTORY_CSV" ]] && echo "timestamp,node,cpu_millicores,mem_bytes,disk_pct,analyst_count,kali_count,load_avg" > "$HISTORY_CSV"
}

log_metrics() {
    local ts=$(date '+%Y-%m-%d %H:%M:%S')
    local cpu=$(kubectl top nodes --no-headers 2>/dev/null | awk '{print $2}' | head -n1 | tr -d 'm' || echo 0)
    [[ -z "$cpu" ]] && cpu=0
    local mem=$(kubectl top nodes --no-headers 2>/dev/null | awk '{print $3}' | head -n1 | tr -d 'Mi' || echo 0)
    [[ -z "$mem" ]] && mem=0
    local disk=$(df -h / | tail -n1 | awk '{print $5}' | tr -d '%')
    local analysts=$(kubectl get pods -n analyst -l app=analyst --no-headers 2>/dev/null | wc -l)
    local kali=$(kubectl get pods -n analyst --no-headers 2>/dev/null | grep -i kali | wc -l)
    local load=$(uptime | awk -F'load average: ' '{print $2}' | cut -d, -f1 | tr -d ' ')

    echo "$ts,all,$cpu,$mem,$disk,$analysts,$kali,$load" >> "$HISTORY_CSV"

    if [[ $analysts -gt 15 ]]; then
        log_error "WARNING: High Analyst Load: $analysts containers"
    fi
}

show_container_resources() {
    echo -e "${BLUE}=== Container / Pod Resource Usage ===${NC}"
    echo -e "\n${YELLOW}Analyst (Ubuntu) Pods:${NC}"
    if kubectl top pods -n analyst -l app=analyst --no-headers &>/dev/null; then
        kubectl top pods -n analyst -l app=analyst --no-headers 2>/dev/null | head -n 12 || echo "No metrics available"
    else
        echo "No metrics available (metrics-server missing or inactive)"
    fi
    
    echo -e "\n${YELLOW}Kali noVNC Pods:${NC}"
    if kubectl top pods -n analyst --no-headers &>/dev/null; then
        kubectl top pods -n analyst --no-headers 2>/dev/null | grep -i kali | head -n 8 || echo "No Kali instances"
    else
        echo "No metrics available (metrics-server missing or inactive)"
    fi
}

show_service_health() {
    echo -e "\n${BLUE}=== Service Health ===${NC}"
    local pending=$(kubectl get pods -A --no-headers 2>/dev/null | grep -E 'Pending|CrashLoopBackOff|Error' | wc -l)
    [[ $pending -gt 0 ]] && echo -e "${RED}⚠ $pending pods in bad state${NC}" || echo -e "${GREEN}✓ Core services healthy${NC}"
}

show_network() {
    echo -e "\n${BLUE}=== Network Bandwidth ===${NC}"
    if command -v ifstat &>/dev/null; then
        local iface=$(ip route | grep default | awk '{print $5}' | head -n1)
        [[ -n "$iface" ]] && ifstat -i "$iface" 1 4 | tail -n 4
    fi
}

show_historical() {
    echo -e "${BLUE}=== Historical Data (Last 25) ===${NC}"
    if [[ -f "$HISTORY_CSV" ]]; then
        tail -n 25 "$HISTORY_CSV" | column -t -s,
    else
        echo "No history log found."
    fi
}

real_time_dashboard() {
    while true; do
        print_header
        show_container_resources
        show_service_health
        show_network
        log_metrics
        echo -e "\n${YELLOW}Refreshing every 8s • Ctrl+C to exit${NC}"
        sleep 8
    done
}

main() {
    init_history
    print_header
    echo -e "${YELLOW}Choose Mode:${NC}"
    echo "1) Real-time Dashboard"
    echo "2) Historical View"
    read -p "Choice [1-2]: " MODE
    [[ "$MODE" == "2" ]] && show_historical || real_time_dashboard
}

main "$@"