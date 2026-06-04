#!/usr/bin/env bash
# scripts/install.sh - Interactive CEI Labs Installer
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/install.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

print_header() {
    clear
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗"
    echo -e "║           CEI Labs Engine - Interactive Installer        ║"
    echo -e "║               Cybersecurity Training Platform            ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
}

log() { echo -e "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

progress_bar() {
    local percent=$1; local width=50
    local filled=$((width * percent / 100))
    printf "\r${BLUE}[%-50s] %3d%%${NC}" "$(printf '█%.0s' $(seq 1 $filled))" "$percent"
}

select_mode() {
    print_header
    echo -e "${YELLOW}Select Installation Mode:${NC}"
    echo "1) Guided  - Full explanations (Beginner)"
    echo "2) General - Recommended (Balanced)"
    echo "3) Advanced - Fast install (Expert)"
    read -p "Enter choice [1-3] (default 2): " MODE_CHOICE
    case $MODE_CHOICE in
        1) MODE="guided" ;;
        3) MODE="advanced" ;;
        *) MODE="general" ;;
    esac
    log "${GREEN}Mode selected: $MODE${NC}"
}

detect_existing() {
    if kubectl get ns ctfd &>/dev/null 2>&1 || [ -f /etc/rancher/k3s/k3s.yaml ]; then
        echo -e "${YELLOW}Existing installation detected!${NC}"
        echo "1) Upgrade (recommended)"
        echo "2) Full Reinstall"
        echo "3) Abort"
        read -p "Choice: " ACTION
        case $ACTION in
            1) return 0 ;;
            2) ./scripts/platform-down.sh || true ;;
            *) exit 0 ;;
        esac
    fi
}

main() {
    print_header
    log "CEI Labs Installation Started"
    select_mode
    detect_existing

    echo -e "${BLUE}[1/8] Checking prerequisites...${NC}"; progress_bar 20
    sudo apt-get update -qq && sudo apt-get install -y git curl python3-pip unzip jq ifstat sysstat

    echo -e "${BLUE}[2/8] Configuration...${NC}"; progress_bar 40
    if [[ ! -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
        cp "$REPO_ROOT/ansible/group_vars/all.yml.example" "$REPO_ROOT/ansible/group_vars/all.yml"
        log "${YELLOW}all.yml created from template${NC}"
        [[ $MODE == "guided" ]] && nano "$REPO_ROOT/ansible/group_vars/all.yml"
    fi

    echo -e "${BLUE}[3/8] Ansible Host Provisioning...${NC}"; progress_bar 60
    ansible-playbook -i ansible/inventory.ini ansible/site.yml

    echo -e "${BLUE}[4/8] Platform Deployment...${NC}"; progress_bar 80
    ./scripts/platform-up.sh

    echo -e "${BLUE}[5/8] Building Analyst Image...${NC}"; progress_bar 95
    docker build -t ctf-analyst:latest -f operator/analyst/Dockerfile operator/analyst || true

    echo -e "${GREEN}Installation Complete!${NC}"
    echo "Next steps: ./scripts/juice-shop-ctf-import.sh → ./scripts/challenges-load.sh → ./scripts/spawn-analysts.sh roster.txt"
}

main "$@"