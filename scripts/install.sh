#!/usr/bin/env bash
# scripts/install.sh - OS-Independent Interactive CEI Labs Installer (Docker Swarm edition)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/install.log"
DOCKER_DIR="$REPO_ROOT/docker"

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

detect_package_manager() {
    if command -v apt-get &>/dev/null; then PM="apt"
    elif command -v dnf &>/dev/null; then PM="dnf"
    elif command -v pacman &>/dev/null; then PM="pacman"
    else PM="unknown"
    fi
}

install_docker_if_missing() {
    if command -v docker &>/dev/null; then
        log_info "Docker already installed."
        return 0
    fi
    log_info "Installing Docker Engine via the official convenience script..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    log_warn "Added ${USER} to the docker group — log out/in (or run 'newgrp docker') for it to take effect this session."
}

handle_dependencies() {
    print_header
    echo -e "${YELLOW}Dependency Management${NC}"
    detect_package_manager
    log_info "Detected package manager: $PM"

    case "$PM" in
        apt)
            sudo apt-get update -qq
            sudo apt-get install -y --no-install-recommends git curl jq unzip
            ;;
        dnf)
            sudo dnf install -y git curl jq unzip
            ;;
        pacman)
            sudo pacman -Sy --noconfirm --needed git curl jq unzip
            ;;
        *)
            log_warn "Unrecognized package manager — ensure git, curl, and jq are installed manually."
            ;;
    esac

    install_docker_if_missing

    if [[ "${MODE:-single}" == "multi" ]]; then
        case "$PM" in
            apt) sudo apt-get install -y python3-pip; command -v ansible &>/dev/null || { sudo add-apt-repository --yes --update ppa:ansible/ansible; sudo apt-get install -y ansible; } ;;
            dnf) command -v ansible &>/dev/null || sudo dnf install -y ansible-core ;;
            pacman) command -v ansible &>/dev/null || sudo pacman -S --noconfirm --needed ansible ;;
            *) command -v ansible &>/dev/null || { log_error "Install Ansible manually for multi-host provisioning."; exit 1; } ;;
        esac
    fi

    log_info "Dependency phase complete."
}

select_mode() {
    print_header
    echo -e "${YELLOW}How many hosts are you provisioning?${NC}"
    echo "1) Single host   - Install Docker here and deploy directly (default)"
    echo "2) Multiple hosts - Use Ansible to provision a fleet and form a Swarm across them"
    read -rp "Choice [1-2] (default 1): " MODE_CHOICE
    MODE_CHOICE=${MODE_CHOICE:-1}
    [[ "$MODE_CHOICE" == "2" ]] && MODE="multi" || MODE="single"
    log_info "Mode: $MODE"
}

configure_docker_env() {
    print_header
    echo -e "${YELLOW}Configuration${NC}"

    if [[ ! -f "$DOCKER_DIR/.env" ]]; then
        cp "$DOCKER_DIR/.env.example" "$DOCKER_DIR/.env"
    fi
    if [[ ! -d "$DOCKER_DIR/secrets" ]]; then
        cp -r "$DOCKER_DIR/secrets.example" "$DOCKER_DIR/secrets"
    fi

    read -rp "Base domain for the platform (default: ctf.local): " DOMAIN
    DOMAIN=${DOMAIN:-ctf.local}
    sed -i "s/^BASE_DOMAIN=.*/BASE_DOMAIN=${DOMAIN}/" "$DOCKER_DIR/.env"

    read -rp "GitHub org/user your images are published under: " ORG
    if [[ -n "$ORG" ]]; then
        sed -i "s/^GITHUB_ORG=.*/GITHUB_ORG=${ORG}/" "$DOCKER_DIR/.env"
    fi

    echo -e "\n${YELLOW}Secrets (leave blank to auto-generate a random value):${NC}"
    for name in ctfd_secret_key ctfd_db_password ctfd_db_root_password plugin_shared_secret orchestrator_admin_password hint_wallet_sync_secret; do
        read -rsp "  ${name}: " VALUE
        echo
        if [[ -z "$VALUE" ]]; then
            VALUE=$(head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32)
            log_info "  generated random value for ${name}"
        fi
        echo "$VALUE" > "$DOCKER_DIR/secrets/${name}.txt"
    done

    read -rp "CTF key (seeds all Juice Shop flags — must be re-used consistently, default: random): " CTF_KEY
    if [[ -z "$CTF_KEY" ]]; then
        CTF_KEY=$(head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32)
    fi
    echo "$CTF_KEY" > "$DOCKER_DIR/secrets/ctf_key.txt"

    # credential_encryption_key must be a valid Fernet key (urlsafe-base64
    # encoded 32 raw bytes) -- app/crypto.py hands it straight to
    # cryptography.fernet.Fernet(), so unlike the secrets in the loop above
    # it can'''t just be an arbitrary alnum string. Generated with Python
    # (already a hard dependency of every image this stack builds) so the
    # encoding always matches what Fernet itself expects.
    if [[ ! -s "$DOCKER_DIR/secrets/credential_encryption_key.txt" ]]; then
        python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"             > "$DOCKER_DIR/secrets/credential_encryption_key.txt" 2>/dev/null             || python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"                 > "$DOCKER_DIR/secrets/credential_encryption_key.txt"
        log_info "  generated random Fernet key for credential_encryption_key"
    fi

    log_info "Configuration written to docker/.env and docker/secrets/."
}

provision_hosts() {
    if [[ "$MODE" == "single" ]]; then
        return 0
    fi
    print_header
    echo -e "${YELLOW}Multi-host provisioning via Ansible${NC}"
    log_warn "Edit ansible/inventory.ini to list your hosts, then press Enter to continue."
    read -rp "Press Enter once ansible/inventory.ini is ready..." _
    ansible-playbook -i "$REPO_ROOT/ansible/inventory.ini" "$REPO_ROOT/ansible/site.yml"
    log_info "Hosts provisioned and Swarm formed."
    log_warn "Run the rest of this installer (or ./scripts/stack-up.sh) FROM the primary manager node —"
    log_warn "docker stack deploy needs local Docker access to a manager."
}

setup_ctfd() {
    print_header
    echo -e "${YELLOW}Automated CTFd Setup${NC}"

    # BASE_DOMAIN isn't set as a shell variable anywhere in this script (only
    # written into docker/.env by configure_docker_env, as $DOMAIN) — read it
    # back explicitly instead of relying on cross-function variable leakage.
    local BASE_DOMAIN
    BASE_DOMAIN=$(grep '^BASE_DOMAIN=' "$DOCKER_DIR/.env" | cut -d= -f2)

    # `docker run --network cei-labs_edge curlimages/curl` (the original
    # approach here) can never work: `edge` is deliberately `attachable:
    # false` in stack.yml, and Traefik's own published HTTPS port already
    # gets us to CTFd without needing to join any Swarm-internal network at
    # all — same approach as the previous host-side curl calls below, just
    # extended to the whole flow so cookies persist naturally across calls
    # (a fresh `docker run --rm` container for each step, as before, would
    # have no continuity for CTFd's session-bound CSRF nonce anyway, even if
    # the network attach issue were fixed separately).
    log_info "Waiting for the CTFd container to come up..."
    for _ in $(seq 1 30); do
        curl -sfk -o /dev/null -H "Host: ctfd.${BASE_DOMAIN}" "https://localhost/setup" && break
        sleep 5
    done

    local cookie_jar nonce
    cookie_jar=$(mktemp)

    # Attribute order in the <input> tag varies by CTFd version (this repo's
    # CTFd 3.8.2 emits id/name/type/value, not name/value adjacent) — match
    # the whole tag first, then pull value= out of it, instead of assuming a
    # fixed order. Verified against a live instance: the old regex here
    # silently returns an empty nonce and the setup POST 403s.
    nonce=$(curl -sk -c "$cookie_jar" -H "Host: ctfd.${BASE_DOMAIN}" "https://localhost/setup" \
        | grep -oE '<input[^>]*name="nonce"[^>]*>' | grep -oE 'value="[a-f0-9]+"' | head -n1 | cut -d'"' -f2)

    read -rp "CTF name (default: CEI Labs Cyber Range): " CTF_NAME
    CTF_NAME=${CTF_NAME:-CEI Labs Cyber Range}
    read -rp "Admin email (default: admin@ctf.local): " ADMIN_EMAIL
    ADMIN_EMAIL=${ADMIN_EMAIL:-admin@ctf.local}
    read -rsp "Admin password: " ADMIN_PASS
    echo

    curl -sk -b "$cookie_jar" -c "$cookie_jar" -H "Host: ctfd.${BASE_DOMAIN}" \
        -X POST "https://localhost/setup" \
        -F "nonce=${nonce}" \
        -F "ctf_name=${CTF_NAME}" \
        -F "name=admin" \
        -F "email=${ADMIN_EMAIL}" \
        -F "password=${ADMIN_PASS}" \
        -F "user_mode=teams" \
        -F "setup=true" > /dev/null

    rm -f "$cookie_jar"

    log_info "CTFd initialized. Log in at https://ctfd.${BASE_DOMAIN} to generate an admin API token,"
    log_info "then: export CTFD_ADMIN_TOKEN=... and run ./scripts/challenges-load.sh"
}

main() {
    print_header
    cd "$REPO_ROOT"

    select_mode
    handle_dependencies

    echo -e "\n${BLUE}[1/5] Preparing local case-file storage...${NC}"
    ./scripts/setup-cases.sh

    echo -e "\n${BLUE}[2/5] Configuring docker/.env and docker/secrets/...${NC}"
    configure_docker_env

    echo -e "\n${BLUE}[3/5] Provisioning hosts...${NC}"
    provision_hosts

    if [[ "$MODE" == "single" ]]; then
        echo -e "\n${BLUE}[4/5] Deploying the stack...${NC}"
        ./scripts/stack-up.sh

        echo -e "\n${BLUE}[5/5] CTFd initialization...${NC}"
        setup_ctfd
    else
        log_warn "Multi-host mode: SSH to your primary manager node and run ./scripts/stack-up.sh there to finish."
    fi

    print_header
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗"
    echo -e "║            CEI Labs Engine Installation Complete          ║"
    echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BLUE}Next Steps:${NC}"
    echo -e "  1. Generate a CTFd admin API token, then: ${YELLOW}export CTFD_ADMIN_TOKEN=...${NC}"
    echo -e "  2. Load challenges:              ${YELLOW}./scripts/challenges-load.sh${NC}"
    echo -e "  3. Import Juice Shop challenges: ${YELLOW}./scripts/juice-shop-ctf-import.sh${NC}"
    echo -e "  4. Bulk-spawn workspaces:        ${YELLOW}./scripts/spawn-workspaces.sh roster.txt${NC}"
    echo -e "  5. Check platform status:        ${YELLOW}./scripts/status.sh${NC}"
    echo ""
}

main "$@"
