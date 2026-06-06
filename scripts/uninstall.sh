#!/usr/bin/env bash
# scripts/uninstall.sh - Interactive Uninstaller
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/uninstall.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

# Core application spaces targeted for standard/safe tenant teardowns
PLATFORM_NAMESPACES=("ctfd" "multijuicer" "analyst")

print_header() {
    clear
    echo -e "${RED}╔══════════════════════════════════════════════════════════╗"
    echo -e "║           CEI Labs Engine - Uninstaller                  ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

select_depth() {
    print_header
    echo -e "${YELLOW}Select Destructive Scope & Uninstall Depth:${NC}"
    echo "1) Safe     - Scope bounded to range namespaces only. Keeps PVCs, database storage, and cases intact. (Recommended)"
    echo "2) Full     - Remove all custom platform components and namespaces completely."
    echo "3) Nuclear  - Wipe local orchestrator infrastructure (K3s) + purge all host-level data pools."
    echo ""
    read -p "Choice [1-3] (default 1): " DEPTH
    DEPTH=${DEPTH:-1}
    case $DEPTH in 
        1) DEPTH_LEVEL="safe";; 
        2) DEPTH_LEVEL="full";; 
        3) DEPTH_LEVEL="nuclear";; 
        *) DEPTH_LEVEL="safe";; 
    esac
}

main() {
    print_header
    log_warn "WARNING: You are about to initiate the uninstallation process for CEI Labs Engine."
    log_warn "Depending on the chosen depth, this action can permanently remove infrastructure, data, and configurations."
    echo ""
    read -p "Type 'DESTROY' to confirm uninstall: " CONFIRM
    [[ "$CONFIRM" != "DESTROY" ]] && { log_info "Teardown canceled by operator."; exit 0; }

    select_depth
    log_info "Uninstall execution pipeline started at depth context: [${DEPTH_LEVEL^^}]"

    if [[ "$DEPTH_LEVEL" == "safe" ]]; then
        # Bounded cleanup loop: deletes user workloads but leaves stateful systems and cluster ingress intact
        log_info "Wiping range execution workloads while preserving persistent volumes and master core infrastructure..."
        for ns in "${PLATFORM_NAMESPACES[@]}"; do
            if kubectl get ns "$ns" &>/dev/null; then
                log_info "Purging stateless components in namespace: ${ns}"
                # Target stateless application layers explicitly inside targeted workspaces
                kubectl delete deployment,ingress,ingressroute,service,daemonset,horizontalpodautoscaler,cronjob --all -n "$ns" --timeout=60s || true
            else
                log_warn "Target namespace '${ns}' not found. Skipping cleanup layer."
            fi
        done
        log_info "Safe-mode isolation cleanup routine finalized successfully."
    fi

    if [[ "$DEPTH_LEVEL" == "full" ]]; then
        log_warn "Executing full range teardown script..."
        if [[ -x "$REPO_ROOT/scripts/platform-down.sh" ]]; then
            "$REPO_ROOT/scripts/platform-down.sh"
        else
            log_error "Platform teardown backend logic missing: scripts/platform-down.sh"
            # Fallback namespace purge to prevent leaving hanging resources
            for ns in "${PLATFORM_NAMESPACES[@]}"; do
                kubectl delete namespace "$ns" --timeout=90s || true
            done
        fi
    fi

    if [[ "$DEPTH_LEVEL" == "nuclear" ]]; then
        log_error "Executing nuclear clearance. Purging all underlying cluster systems and configurations..."
        
        if [[ -x "$REPO_ROOT/scripts/platform-down.sh" ]]; then
            "$REPO_ROOT/scripts/platform-down.sh" || true
        fi
        
        if [[ -f "/usr/local/bin/k3s-uninstall.sh" ]]; then
            log_warn "Invoking K3s local host uninstallation agent..."
            sudo /usr/local/bin/k3s-uninstall.sh || true
        fi
        
        log_warn "Cleaning system configuration directory records..."
        sudo rm -rf /etc/rancher /var/lib/rancher /var/openebs "$REPO_ROOT/status-history.csv"
        
        read -p "Do you want to purge local persistent data cases inside /opt/ctf-cases? [y/N]: " PURGE_CASES
        if [[ "${PURGE_CASES,,}" == "y" ]]; then
            log_error "Purging local data mounts inside /opt/ctf-cases..."
            sudo rm -rf /opt/ctf-cases
        fi
    fi

    log_info "Uninstall processing lifecycle completed."
}

main "$@"