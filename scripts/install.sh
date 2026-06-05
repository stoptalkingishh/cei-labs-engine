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

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

verify_dependencies() {
    local missing_deps=()
    for cmd in kubectl helm ansible jq curl; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_deps+=("$cmd")
        fi
    done
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "FATAL: Missing required core dependencies: ${missing_deps[*]}"
        log_warn "Please install the missing binaries before executing the range platform wizard."
        exit 1
    fi
}

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
    log_info "Mode selected: $MODE"
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
    verify_dependencies
    print_header
    log_info "CEI Labs Installation Started"
    select_mode
    detect_existing

    echo -e "${BLUE}[1/8] Checking prerequisites...${NC}"; progress_bar 20
    sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends git curl python3-pip unzip jq ifstat sysstat

    echo -e "${BLUE}[2/8] Configuration...${NC}"; progress_bar 40
    # Expanded validation pattern matching to ensure critical deployment parameters exist in addition to baseline versions
    local config_pattern="k3s_version\|ctfd_version\|multijuicer_version\|deployment_mode\|use_metallb\|multijuicer_max_instances\|multijuicer_ctf_key\|multijuicer_admin_password"
    
    # Create file if missing, or recreate if it exists but is incomplete (e.g., missing expected structure or keys)
    if [[ ! -f "$REPO_ROOT/ansible/group_vars/all.yml" ]] || ! grep -q "$config_pattern" "$REPO_ROOT/ansible/group_vars/all.yml" 2>/dev/null; then
        if [[ -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
            log_warn "Existing all.yml detected but found to be incomplete. Backing up and renewing from template..."
            if cp "$REPO_ROOT/ansible/group_vars/all.yml" "$REPO_ROOT/ansible/group_vars/all.yml.bak" 2>/dev/null; then
                log_info "Backup created successfully at: $REPO_ROOT/ansible/group_vars/all.yml.bak"
            else
                log_error "Failed to back up existing all.yml file"
            fi
        fi
        cp "$REPO_ROOT/ansible/group_vars/all.yml.example" "$REPO_ROOT/ansible/group_vars/all.yml"
        log_warn "all.yml created from template"
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