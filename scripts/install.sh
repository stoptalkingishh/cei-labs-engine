#!/usr/bin/env bash
# scripts/install.sh - OS-Independent Interactive CEI Labs Installer
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

log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

progress_bar() {
    local percent=$1; local width=50
    local filled=$((width * percent / 100))
    printf "\r${BLUE}[%-50s] %3d%%${NC}" "$(printf '█%.0s' $(seq 1 $filled))" "$percent"
}

detect_package_manager() {
    if command -v apt-get &>/dev/null; then
        PM="apt"
    elif command -v dnf &>/dev/null; then
        PM="dnf"
    elif command -v pacman &>/dev/null; then
        PM="pacman"
    else
        PM="unknown"
    fi
}

handle_dependencies() {
    print_header
    echo -e "${YELLOW}Dependency Management Lifecycle${NC}"
    echo "1) Automated Install - Script will detect OS and install Ansible, Docker, and utilities"
    echo "2) Manual Validation - Verify environment dependencies are already manually managed"
    read -p "Select option [1-2] (default 1): " DEP_CHOICE
    DEP_CHOICE=${DEP_CHOICE:-1}

    if [[ "$DEP_CHOICE" == "1" ]]; then
        detect_package_manager
        log_info "Detected Package Manager: $PM"
        
        case "$PM" in
            apt)
                log_info "Updating apt package lists..."
                sudo apt-get update -qq
                log_info "Installing core systems utilities..."
                sudo apt-get install -y --no-install-recommends git curl python3-pip python3-venv unzip jq ifstat sysstat software-properties-common
                
                if ! command -v docker &>/dev/null; then
                    log_info "Installing Docker via apt..."
                    sudo apt-get install -y docker.io
                fi
                if ! command -v ansible &>/dev/null; then
                    log_info "Installing Ansible via official PPA..."
                    sudo add-apt-repository --yes --update ppa:ansible/ansible
                    sudo apt-get install -y ansible
                fi
                ;;
            dnf)
                log_info "Optimizing dnf repositories..."
                sudo dnf check-update || true
                log_info "Installing core system utilities..."
                sudo dnf install -y git curl python3-pip unzip jq epel-release
                sudo dnf install -y sysstat || true
                
                if ! command -v docker &>/dev/null; then
                    log_info "Installing Docker via dnf..."
                    sudo dnf install -y docker moby-engine || sudo dnf install -y docker-ce
                fi
                if ! command -v ansible &>/dev/null; then
                    log_info "Installing Ansible via dnf..."
                    sudo dnf install -y ansible-core || sudo dnf install -y ansible
                fi
                ;;
            pacman)
                log_info "Syncing pacman database repositories..."
                sudo pacman -Sy --noconfirm
                log_info "Installing core system packages..."
                sudo pacman -S --noconfirm --needed git curl python-pip unzip jq sysstat
                
                if ! command -v docker &>/dev/null; then
                    log_info "Installing Docker via pacman..."
                    sudo pacman -S --noconfirm --needed docker
                fi
                if ! command -v ansible &>/dev/null; then
                    log_info "Installing Ansible via pacman..."
                    sudo pacman -S --noconfirm --needed ansible
                fi
                ;;
            *)
                log_warn "Unsupported package manager or OS layout detected."
                log_warn "Attempting universal fallback installation via Python PIP for Ansible infrastructure..."
                if command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
                    export PIP_BREAK_SYSTEM_PACKAGES=1
                    pip3 install --upgrade pip setuptools || true
                    pip3 install ansible jq || pip install ansible jq
                else
                    log_error "FATAL: Python Pip missing. Cannot complete automated fallback deployment."
                    exit 1
                fi
                ;;
        esac

        if command -v systemctl &>/dev/null; then
            log_info "Enabling and starting Docker daemon service structures..."
            sudo systemctl enable --now docker || true
        fi
        log_info "Automated dependency phase complete."
    else
        log_info "Executing cross-platform dependency validation check..."
        local missing_deps=()
        for cmd in ansible ansible-playbook jq curl git docker; do
            if ! command -v "$cmd" &>/dev/null; then
                missing_deps+=("$cmd")
            fi
        done
        if [[ ${#missing_deps[@]} -gt 0 ]]; then
            log_error "FATAL: Validation failed. Missing required binaries: ${missing_deps[*]}"
            log_warn "Please rerun the installer and select 'Automated Install' or manually provision these tools."
            exit 1
        fi
        log_info "Manual dependency tracking verification passed."
    fi
}

select_mode() {
    print_header
    echo -e "${YELLOW}Select Range Orchestration Complexity Profile:${NC}"
    echo "1) Simple   - Single-host deployment blueprint (Great for local VMs / quick evaluation)"
    echo "2) Advanced - Distributed node architectures, HA cluster profiles, and scaling matrices"
    read -p "Enter choice [1-2] (default 1): " MODE_CHOICE
    MODE_CHOICE=${MODE_CHOICE:-1}
    
    if [[ "$MODE_CHOICE" == "2" ]]; then
        MODE="advanced"
    else
        MODE="simple"
    fi
    log_info "Complexity profile locked: $MODE"
}

detect_existing() {
    if [ -f /etc/rancher/k3s/k3s.yaml ] || command -v kubectl &>/dev/null && kubectl get ns ctfd &>/dev/null 2>&1; then
        echo -e "${YELLOW}Existing CEI Labs cluster or K3s daemon layer detected!${NC}"
        echo "1) Upgrade Cluster (Preserve state)"
        echo "2) Full Reinstall  (Purge and reinitialize)"
        echo "3) Abort Lifecycle"
        read -p "Choice [1-3] (default 1): " ACTION
        ACTION=${ACTION:-1}
        case $ACTION in
            1) log_info "Proceeding with localized upgrade pipeline..."; return 0 ;;
            2) log_warn "Purging core platform namespaces..."; ./scripts/platform-down.sh || true ;;
            *) log_warn "Installation aborted by operator."; exit 0 ;;
        esac
    fi
}

interactive_configuration() {
    print_header
    echo -e "${YELLOW}Interactive Cluster Configuration Blueprint${NC}"
    
    if [[ ! -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
        cp "$REPO_ROOT/ansible/group_vars/all.yml.example" "$REPO_ROOT/ansible/group_vars/all.yml"
    fi
    if [[ ! -f "$REPO_ROOT/ansible/inventory.ini" ]]; then
        cp "$REPO_ROOT/ansible/inventory.ini.example" "$REPO_ROOT/ansible/inventory.ini" 2>/dev/null || tr -d '\r' <<EOF > "$REPO_ROOT/ansible/inventory.ini"
[initial_master_node]
localhost ansible_connection=local
EOF
    fi

    if [[ "$MODE" == "simple" ]]; then
        log_info "Applying Simple Mode defaults: Single-host localhost topology configuration."
        tr -d '\r' <<EOF > "$REPO_ROOT/ansible/inventory.ini"
[initial_master_node]
localhost ansible_connection=local

[master_nodes]

[worker_nodes]
EOF
        sed -i 's/deployment_mode:.*/deployment_mode: "single"/' "$REPO_ROOT/ansible/group_vars/all.yml"
        sed -i 's/use_metallb:.*/use_metallb: false/' "$REPO_ROOT/ansible/group_vars/all.yml"
        
    else
        log_warn "Advanced Installation Wizard Active"
        echo "Please specify targeted infrastructure layout metrics:"
        
        read -p "Enter Target Deployment Mode [single/dual/cluster] (default: cluster): " TARGET_MODE
        TARGET_MODE=${TARGET_MODE:-cluster}
        sed -i "s/deployment_mode:.*/deployment_mode: \"$TARGET_MODE\"/" "$REPO_ROOT/ansible/group_vars/all.yml"
        
        read -p "Deploy MetalLB bare-metal layer 2 LoadBalancer? [true/false] (default: true): " USE_LB
        USE_LB=${USE_LB:-true}
        sed -i "s/use_metallb:.*/use_metallb: $USE_LB/" "$REPO_ROOT/ansible/group_vars/all.yml"

        echo -e "${BLUE}[!] Launching text editor to map cluster node networks inside ansible/inventory.ini...${NC}"
        sleep 2
        
        local editor="nano"
        command -v vi &>/dev/null && editor="vi"
        command -v nano &>/dev/null && editor="nano"
        $editor "$REPO_ROOT/ansible/inventory.ini"
    fi

    echo -e "\n${YELLOW}Security Token Customization Matrix:${NC}"
    
    read -p "Set CTFd Admin Secret Token Key (Leave blank to generate randomized string): " SEC_KEY
    if [[ -z "$SEC_KEY" ]]; then
        SEC_KEY=$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 32)
        log_info "Generated randomized CTFd Secret Key: $SEC_KEY"
    fi
    sed -i "s/ctfd_secret_key:.*/ctfd_secret_key: \"$SEC_KEY\"/" "$REPO_ROOT/ansible/group_vars/all.yml"

    read -p "Set Central MariaDB Instance Root Password (default: ChangeMeStrongly): " DB_ROOT
    DB_ROOT=${DB_ROOT:-ChangeMeStrongly}
    sed -i "s/ctfd_db_root_password:.*/ctfd_db_root_password: \"$DB_ROOT\"/" "$REPO_ROOT/ansible/group_vars/all.yml"

    read -p "Set MultiJuicer Challenge Dashboard Admin Password (default: JuiceAdminSec): " JUICE_PASS
    JUICE_PASS=${JUICE_PASS:-JuiceAdminSec}
    sed -i "s/multijuicer_admin_password:.*/multijuicer_admin_password: \"$JUICE_PASS\"/" "$REPO_ROOT/ansible/group_vars/all.yml"
}

main() {
    print_header
    handle_dependencies
    select_mode
    detect_existing
    interactive_configuration

    print_header
    log_info "CEI Labs Engine Deployment Commencing..."
    
    echo -e "\n${BLUE}[3/6] Running Local Case File Directory Blueprinting...${NC}"; progress_bar 30
    ./scripts/setup-cases.sh

    echo -e "\n${BLUE}[4/6] Executing Ansible Host Core Infrastructure Playbook...${NC}"; progress_bar 55
    ansible-playbook -i ansible/inventory.ini ansible/site.yml

    echo -e "\n${BLUE}[5/6] Triggering K3s Platform Helm App Deployments...${NC}"; progress_bar 80
    ./scripts/platform-up.sh

    echo -e "\n${BLUE}[6/6] Building Sandbox Workspace Analyst Tooling Images...${NC}"; progress_bar 95
    docker build -t ctf-analyst:latest -f operator/analyst/Dockerfile operator/analyst || log_warn "Docker build skipped or failed. Local development image execution non-blocking."

    print_header
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗"
    echo -e "║            CEI Labs Cluster Engine Synchronized!         ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
    echo -e "Deployment complete. Finalize range seeding rules with these utilities:\n"
    echo -e "  1. Ingest OWASP Challenge Schemes: ${YELLOW}./scripts/juice-shop-ctf-import.sh${NC}"
    echo -e "  2. Populate Target Curriculum Blocks: ${YELLOW}./scripts/challenges-load.sh${NC}"
    echo -e "  3. Real-Time Resource Health Dashboard: ${YELLOW}./scripts/status.sh${NC}\n"
}

main "$@"