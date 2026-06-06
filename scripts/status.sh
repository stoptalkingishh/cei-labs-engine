#!/usr/bin/env bash
# scripts/status.sh - Advanced CEI Labs Telemetry & Routing Dashboard
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/status.log"
HISTORY_CSV="$REPO_ROOT/status-history.csv"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'

log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

print_header() {
    clear
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗"
    echo -e "║      CEI Labs Engine - Advanced System Monitoring        ║"
    echo -e "║                  $(date '+%Y-%m-%d %H:%M:%S')                     ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

init_history() {
    [[ ! -f "$HISTORY_CSV" ]] && echo "timestamp,node,cpu_millicores,mem_bytes,disk_pct,analyst_count,kali_count,load_avg" > "$HISTORY_CSV"
}

log_metrics() {
    local ts cpu mem disk analysts kali load
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    cpu=$(kubectl top nodes --no-headers 2>/dev/null | awk '{print $2}' | head -n1 | tr -d 'm' || echo 0)
    [[ -z "$cpu" ]] && cpu=0
    mem=$(kubectl top nodes --no-headers 2>/dev/null | awk '{print $3}' | head -n1 | tr -d 'Mi' || echo 0)
    [[ -z "$mem" ]] && mem=0
    disk=$(df -h / | tail -n1 | awk '{print $5}' | tr -d '%')
    analysts=$(kubectl get pods -n analyst -l app=analyst --no-headers 2>/dev/null | wc -l)
    kali=$(kubectl get pods -n analyst --no-headers 2>/dev/null | grep -i kali | wc -l)
    load=$(uptime | awk -F'load average: ' '{print $2}' | cut -d, -f1 | tr -d ' ' || echo "0.00")

    echo "$ts,all,$cpu,$mem,$disk,$analysts,$kali,$load" >> "$HISTORY_CSV"

    if [[ $analysts -gt 15 ]]; then
        log_error "WARNING: High Analyst Load: $analysts containers"
    fi
}

show_host_resources() {
    echo -e "${BLUE}=== 1. Bare-Metal Host Infrastructure Matrix ===${NC}"
    
    # Active Daemon Status Tracking
    local k3s_state; k3s_state=$(systemctl is-active k3s 2>/dev/null || echo "inactive")
    local ufw_state; ufw_state=$(sudo ufw status 2>/dev/null | head -n1 | awk '{print $2}' || echo "unknown")
    
    printf "  %-18s: " "K3s Orchestrator"
    [[ "$k3s_state" == "active" ]] && echo -e "${GREEN}✓ Active / Running${NC}" || echo -e "${RED}✗ Inactive / Stopped${NC}"
    
    printf "  %-18s: " "UFW Firewall"
    [[ "$ufw_state" == "active" ]] && echo -e "${GREEN}✓ Active${NC}" || echo -e "${YELLOW}! Inactive / Bypass Mode${NC}"

    # Resource Capacity Calculations
    local host_cpu; host_cpu=$(top -bn1 | grep "Cpu(s)" | awk '{print $2 + $4}')
    local host_mem; host_mem=$(free -m | awk '/Mem:/ {printf "%.1f%% (%dMB / %dMB)", $3/$2*100, $3, $2}')
    local case_storage; case_storage=$(df -h /opt/ctf-cases 2>/dev/null | tail -n1 | awk '{print $5" used ("$4" remaining)"}' || echo "N/A")

    printf "  %-18s: %s%%\n" "Host CPU Load" "$host_cpu"
    printf "  %-18s: %s\n" "Host Memory Pool" "$host_mem"
    printf "  %-18s: %s\n" "/opt/ctf-cases Space" "$case_storage"
    echo ""
}

show_service_routing() {
    echo -e "${BLUE}=== 2. Network Service Access & Routing Matrix ===${NC}"
    local lb_ip
    lb_ip=$(kubectl get svc -n traefik traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
    
    if [[ -z "$lb_ip" ]]; then
        lb_ip=$(hostname -I | awk '{print $1}')
        echo -e "  ${YELLOW}Mode Notice: No LoadBalancer assigned. Utilizing Primary Host IP fallback.${NC}"
    fi

    printf "  %-22s -> %s\n" "CTFd Scoreboard" "http://${lb_ip} (Port 80/443)"
    printf "  %-22s -> %s\n" "MultiJuicer Portal" "http://${lb_ip}/balancer"
    printf "  %-22s -> %s\n" "Internal Core Registry" "http://${lb_ip}:5000"
    
    echo -e "\n  ${YELLOW}Active Dynamic Ingress Routes (Traefik Map):${NC}"
    if kubectl get ingressroutes -A &>/dev/null; then
        kubectl get ingressroutes -A -o custom-columns="NAMESPACE:.metadata.namespace,NAME:.metadata.name,MATCH:.spec.routes[*].match" | sed 's/^/    /'
    else
        echo "    No custom IngressRoute definitions discovered."
    fi
    echo ""
}

show_cluster_health() {
    echo -e "${BLUE}=== 3. Microservice Pod Health Context ===${NC}"
    
    # Calculate pod statuses across core workspaces
    local running pending crashed
    running=$(kubectl get pods -A --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
    pending=$(kubectl get pods -A --field-selector=status.phase=Pending --no-headers 2>/dev/null | wc -l)
    crashed=$(kubectl get pods -A --no-headers 2>/dev/null | grep -E 'CrashLoopBackOff|Error|ImagePullBackOff' | wc -l || echo 0)

    printf "  Running Pods Pool : %s\n" "$running"
    printf "  Pending Pods Pool : %s\n" "$pending"
    
    if [[ $crashed -gt 0 ]]; then
        printf "  Degraded Pod State: ${RED}%s pods in failed status loops${NC}\n" "$crashed"
        echo -e "  ${RED}Targeting Fault Vectors:${NC}"
        kubectl get pods -A --no-headers 2>/dev/null | grep -E 'CrashLoopBackOff|Error|ImagePullBackOff' | awk '{print "    - ["$1"] "$2" ("$4")"}'
    else
        printf "  Degraded Pod State: ${GREEN}0 Failures Documented${NC}\n"
    fi
    echo ""
}

show_ctfd_reachability() {
    echo -e "${BLUE}=== 4. Scoreboard Ingress Accessibility Check ===${NC}"
    local lb_ip status_code
    lb_ip=$(kubectl get svc -n traefik traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
    [[ -z "$lb_ip" ]] && lb_ip="127.0.0.1"

    status_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://${lb_ip}" || echo "000")
    
    printf "  Direct L4 Ingress Verification (http://%s): " "$lb_ip"
    if [[ "$status_code" == "200" || "$status_code" == "302" ]]; then
        echo -e "${GREEN}✓ HTTP ${status_code} (Reachable)${NC}"
    elif [[ "$status_code" == "000" ]]; then
        echo -e "${RED}✗ Drop Event / Connection Refused${NC}"
    else
        echo -e "${YELLOW}! HTTP ${status_code} (Unexpected return code / Ingress route anomaly)${NC}"
    fi
    echo ""
}

show_analyst_workspaces() {
    echo -e "${BLUE}=== 5. Ephemeral Student Analyst Workspaces ===${NC}"
    
    local running_ubuntu running_kali
    running_ubuntu=$(kubectl get pods -n analyst -l app=analyst --no-headers 2>/dev/null | grep -v -i kali | grep -c "Running" || echo 0)
    running_kali=$(kubectl get pods -n analyst --no-headers 2>/dev/null | grep -i kali | grep -c "Running" || echo 0)

    printf "  Active Dedicated Ubuntu Hosts: %s\n" "$running_ubuntu"
    printf "  Active Graphical Kali Instances: %s\n" "$running_kali"
    
    if [[ $((running_ubuntu + running_kali)) -gt 0 ]]; then
        echo -e "\n  ${YELLOW}Active Sandbox Node Directory Assignments:${NC}"
        printf "    %-15s %-35s %-12s\n" "NAMESPACE" "POD NAME" "STATUS"
        kubectl get pods -n analyst -o custom-columns="NAMESPACE:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase" --no-headers | sed 's/^/    /'
    else
        echo -e "  ${GREEN}✓ Range is currently idle. No sandboxes allocated.${NC}"
    fi
}

show_historical() {
    print_header
    echo -e "${BLUE}=== Historical System Logging Timeline (Last 25 Checks) ===${NC}"
    if [[ -f "$HISTORY_CSV" ]]; then
        tail -n 25 "$HISTORY_CSV" | column -t -s,
    else
        echo "No historical logging archive discovered."
    fi
}

real_time_dashboard() {
    while true; do
        print_header
        show_host_resources
        show_service_routing
        show_cluster_health
        show_ctfd_reachability
        show_analyst_workspaces
        log_metrics
        echo -e "\n${YELLOW}Refreshing engine status page every 8 seconds • Break execution via Ctrl+C${NC}"
        sleep 8
    done
}

main() {
    init_history
    print_header
    echo -e "${YELLOW}Select Telemetry Operation Mode:${NC}"
    echo "1) Real-time Dashboard Core Interface"
    echo "2) Historical Metric Analysis Ledger"
    read -p "Choice [1-2]: " MODE
    [[ "${MODE:-1}" == "2" ]] && show_historical || real_time_dashboard
}

main "$@"