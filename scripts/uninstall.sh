#!/usr/bin/env bash
# scripts/uninstall.sh - Interactive Uninstaller
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

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

select_depth() {
    print_header
    echo -e "${YELLOW}Uninstall Depth:${NC}"
    echo "1) Safe     - Keep PVCs, databases, cases (Recommended)"
    echo "2) Full     - Remove all platform components"
    echo "3) Nuclear  - Remove K3s + all data (Destructive)"
    read -p "Choice [1-3]: " DEPTH
    case $DEPTH in 1) DEPTH_LEVEL="safe";; 2) DEPTH_LEVEL="full";; 3) DEPTH_LEVEL="nuclear";; *) DEPTH_LEVEL="safe";; esac
}

main() {
    print_header
    log_warn "WARNING: You are about to initiate the uninstallation process for CEI Labs Engine."
    log_warn "Depending on the chosen depth, this action can permanently remove infrastructure, data, and configurations."
    read -p "Type 'DESTROY' to confirm uninstall: " CONFIRM
    [[ "$CONFIRM" != "DESTROY" ]] && exit 0

    select_depth
    log_info "Uninstall started at depth: $DEPTH_LEVEL"

    if [[ $DEPTH_LEVEL == "safe" ]]; then
        # Safe mode preserves stateful resources (PVCs/PVs) by selectively deleting deployments/statefulsets while keeping data intact
        log_warn "Removing standard workload controllers while preserving stateful resource definitions..."
        kubectl delete deployment,ingress,service,daemonset,horizontalpodautoscaler,cronjob --all -A || true
    fi

    if [[ $DEPTH_LEVEL == "full" ]]; then
        log_warn "Executing full range teardown script..."
        ./scripts/platform-down.sh
    fi

    if [[ $DEPTH_LEVEL == "nuclear" ]]; then
        log_error "Executing nuclear clearance. Purging all underlying cluster systems and configurations..."
        ./scripts/platform-down.sh
        if [[ -f "/usr/local/bin/k3s-uninstall.sh" ]]; then
            sudo /usr/local/bin/k3s-uninstall.sh || true
        fi
        sudo rm -rf /etc/rancher /var/lib/rancher
    fi

    log_info "Uninstall completed."
}

main "$@"