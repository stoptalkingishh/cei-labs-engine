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
                    pushd "$REPO_ROOT" >/dev/null
                    pip3 install --upgrade pip setuptools || true
                    pip3 install ansible jq || pip install ansible jq
                    popd >/dev/null
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
    if [ -f /etc/rancher/k3s/k3s.yaml ] || { command -v kubectl &>/dev/null && kubectl get ns ctfd &>/dev/null 2>&1; }; then
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

setup_ctfd() {
    print_header
    echo -e "${YELLOW}Automated Headless CTFd Priming Engine Launching...${NC}"
    echo ""

    # 1. Block wait until the CTFd container service is up internally
    log_info "Waiting for internal CTFd pod infrastructure to state settle..."
    while true; do
        local pod_status
        pod_status=$(kubectl get pods -n ctfd -l app.kubernetes.io/name=ctfd -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Missing")
        if [[ "$pod_status" == "Running" ]]; then
            log_info "Target microservice pod online. Validating network routing availability..."
            break
        fi
        sleep 4
    done

    # 2. Extract the cluster setup token via the internal service route
    local cluster_ip cookie_jar nonce
    cluster_ip=$(kubectl get svc -n ctfd ctfd -o jsonpath='{.spec.clusterIP}')
    cookie_jar=$(mktemp)

    log_info "Extracting state tracking metadata synchronization block tokens..."
    
    # Poll internal service loop until valid data returns
    until curl -s -c "$cookie_jar" "http://${cluster_ip}/setup" | grep -q "nonce"; do
        sleep 2
    done

    # Scrape the direct raw input csrf nonce token value fields
    nonce=$(curl -s -b "$cookie_jar" "http://${cluster_ip}/setup" | grep -oE 'name="nonce" value="[a-f0-9]+"' | awk -F'"' '{print $4}' | head -n1)

    # Re-verify local variables passwords are safe
    local admin_pass; admin_pass=${DB_ROOT:-ChangeMeStrongly}

    # 3. Submit programmatic DB Priming configuration to CTFd
    log_info "Injecting baseline schema models directly into the database container layer..."
    curl -s -b "$cookie_jar" -c "$cookie_jar" \
        -X POST "http://${cluster_ip}/setup" \
        -F "nonce=${nonce}" \
        -F "ctf_name=CEI Labs Cyber Range" \
        -F "name=admin" \
        -F "email=admin@ctf.local" \
        -F "password=${admin_pass}" \
        -F "user_mode=teams" \
        -F "setup=true" > /dev/null

    # 4. Generate dynamic API authorization bearer token profiles headless
    log_info "Generating live API access management tokens..."
    
    # Fetch a fresh session nonce from the settings menu page
    nonce=$(curl -s -b "$cookie_jar" "http://${cluster_ip}/admin/settings" | grep -oE 'name="nonce" value="[a-f0-9]+"' | awk -F'"' '{print $4}' | head -n1)
    
    local api_response token_payload
    api_response=$(curl -s -b "$cookie_jar" \
        -X POST "http://${cluster_ip}/api/v1/tokens" \
        -H "Content-Type: application/json" \
        -H "CSRF-Token: ${nonce}" \
        -d "{\"description\":\"CHALLENGE_LOADER\", \"expiration\":\"\"}")

    token_payload=$(echo "$api_response" | jq -r '.data.value // empty')

    if [[ -z "$token_payload" ]]; then
        log_error "FATAL: Automated token generation dropped out. Check database log targets."
        exit 1
    fi

    # 5. Lock configuration parameters into variable matrix files
    log_info "Syncing generated token payload across all.yml configuration definitions..."
    sed -i "s/ctfd_admin_token:.*/ctfd_admin_token: \"$token_payload\"/" "$REPO_ROOT/ansible/group_vars/all.yml"
    log_info "Configuration variable file synchronized securely."

    # 6. Kick off core content curriculum initialization steps automatically
    log_info "Seeding range target environments..."
    if [[ -x "$REPO_ROOT/scripts/juice-shop-ctf-import.sh" ]]; then
        log_info "Loading MultiJuicer vulnerability curriculum matrices..."
        "$REPO_ROOT/scripts/juice-shop-ctf-import.sh" || log_warn "Juice Shop map step threw exit codes."
    fi

    if [[ -x "$REPO_ROOT/scripts/challenges-load.sh" ]]; then
        log_info "Loading master challenge training track content sets..."
        "$REPO_ROOT/scripts/challenges-load.sh" --sprint 2 || log_warn "Sprint 2 load warning."
        "$REPO_ROOT/scripts/challenges-load.sh" --sprint 3 || log_warn "Sprint 3 load warning."
    fi

    rm -f "$cookie_jar"
    log_info "Headless system application initialization sequences finalized."
}

verify_network_routing() {
    print_header
    echo -e "${YELLOW}Network Routing Verification${NC}"
    echo ""

    local lb_ip
    lb_ip=$(kubectl get svc -n traefik traefik \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")

    echo -e "${BLUE}Service Access Points:${NC}"
    printf "  %-25s %s\n" "CTFd Scoreboard:" "http://${lb_ip}  (and http://ctfd.ctf.local)"
    printf "  %-25s %s\n" "MultiJuicer Admin:" "http://${lb_ip}/balancer/admin  (and http://juiceshop.ctf.local/balancer/admin)"
    printf "  %-25s %s\n" "Container Registry:" "http://registry.ctf.local:5000"
    echo ""

    if [[ "$lb_ip" == "pending" ]]; then
        log_warn "MetalLB IP not yet assigned. Check: kubectl get svc -n traefik traefik"
    else
        log_info "MetalLB IP assigned: ${lb_ip}"
        if curl -sf --max-time 5 "http://${lb_ip}" > /dev/null 2>&1; then
            log_info "CTFd is reachable at http://${lb_ip}"
        else
            log_warn "CTFd not yet responding at http://${lb_ip} — may still be starting."
            log_warn "Check: kubectl get pods -n ctfd"
        fi
    fi

    echo ""
    echo -e "${YELLOW}For LAN access without DNS:${NC}"
    echo "  Add to /etc/hosts on each participant machine:"
    echo "     ${lb_ip}   ctfd.ctf.local juiceshop.ctf.local registry.ctf.local"
    echo ""
    echo "  Or just use the IP directly: http://${lb_ip}"
}

main() {
    print_header
    
    # Pre-emptively cache sudo permissions interactively via the host's terminal wrapper
    echo -e "${YELLOW}[!] Authenticating deployment terminal host permissions...${NC}"
    sudo -v
    cd "$REPO_ROOT"

    handle_dependencies
    select_mode
    detect_existing
    interactive_configuration

    print_header
    log_info "CEI Labs Engine Deployment Commencing..."
    
    echo -e "\n${BLUE}[3/7] Running Local Case File Directory Blueprinting...${NC}"; progress_bar 30
    ./scripts/setup-cases.sh

    echo -e "\n${BLUE}[4/7] Executing Ansible Host Core Infrastructure Playbook...${NC}"; progress_bar 55
    
    # Executes playbook with inherited environment privilege tracking tokens (-E)
    # This prevents the subshell from throwing new interactive string-matching challenge loops
    ANSIBLE_INTERPRETER_PYTHON=auto_silent \
    sudo -E ansible-playbook \
        -i "$REPO_ROOT/ansible/inventory.ini" \
        "$REPO_ROOT/ansible/site.yml"

    echo -e "\n${BLUE}[5/7] Triggering K3s Platform Helm App Deployments...${NC}"; progress_bar 80
    ./scripts/platform-up.sh

    echo -e "\n${BLUE}[6/7] Verifying Network Routing Configuration...${NC}"; progress_bar 90
    verify_network_routing

    echo -e "\n${BLUE}[7/7] CTFd Initialization Guidance...${NC}"; progress_bar 95
    setup_ctfd

    local final_ip
    final_ip=$(kubectl get svc -n traefik traefik \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "see: kubectl get svc -n traefik traefik")

    print_header
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗"
    echo -e "║            CEI Labs Cluster Engine Synchronized!         ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BLUE}Access Points:${NC}"
    echo -e "  CTFd Scoreboard:    ${GREEN}http://${final_ip}${NC}"
    echo -e "  MultiJuicer Admin:  ${GREEN}http://${final_ip}/balancer/admin${NC}"
    echo -e "  With DNS:           ${YELLOW}ctfd.ctf.local / juiceshop.ctf.local${NC}"
    echo ""
    echo -e "${BLUE}Next Steps:${NC}"
    echo -e "  1. Complete CTFd setup wizard if not done"
    echo -e "  2. Import Juice Shop challenges: ${YELLOW}./scripts/juice-shop-ctf-import.sh${NC}"
    echo -e "  3. Load OTW challenges:          ${YELLOW}./scripts/challenges-load.sh${NC}"
    echo -e "  4. Spawn analyst workspaces:     ${YELLOW}./scripts/spawn-analysts.sh roster.txt${NC}"
    echo -e "  5. Check platform status:         ${YELLOW}./scripts/status.sh${NC}"
    echo ""
}

main "$@"