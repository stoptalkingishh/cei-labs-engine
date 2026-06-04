#!/usr/bin/env bash
# scripts/uninstall.sh - Interactive Uninstaller
set -euo pipefail

REPO_ROOT="`\((cd "\)`(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/uninstall.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

print_header() {
    clear
    echo -e "${RED}╔══════════════════════════════════════════════════════════╗"
    echo -e "║           CEI Labs Engine - Uninstaller                  ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

select_depth() {
    print_header
    echo -e "`\({YELLOW}Uninstall Depth:\)`{NC}"
    echo "1) Safe     - Keep PVCs, databases, cases (Recommended)"
    echo "2) Full     - Remove all platform components"
    echo "3) Nuclear  - Remove K3s + all data (Destructive)"
    read -p "Choice [1-3]: " DEPTH
    case $DEPTH in 1) DEPTH_LEVEL="safe";; 2) DEPTH_LEVEL="full";; 3) DEPTH_LEVEL="nuclear";; *) DEPTH_LEVEL="safe";; esac
}

main() {
    print_header
    read -p "${RED}Type 'DESTROY' to confirm uninstall: ${NC}" CONFIRM
    [[ "$CONFIRM" != "DESTROY" ]] && exit 0

    select_depth
    log "Uninstall started at depth: $DEPTH_LEVEL"

    if [[ $DEPTH_LEVEL == "safe" || $DEPTH_LEVEL == "full" ]]; then
        ./scripts/platform-down.sh
    fi

    if [[ $DEPTH_LEVEL == "nuclear" ]]; then
        ./scripts/platform-down.sh
        sudo /usr/local/bin/k3s-uninstall.sh || true
        sudo rm -rf /etc/rancher /var/lib/rancher
    fi

    echo -e "`\({GREEN}Uninstall completed.\)`{NC}"
}

main "$@"