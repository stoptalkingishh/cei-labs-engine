#!/usr/bin/env bash
# scripts/repair.sh - Diagnostic & Repair Tool
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$HOME/cei-backups/repair_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$REPO_ROOT/repair.log"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "[$(date '+%H:%M:%S')] ${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "[$(date '+%H:%M:%S')] ${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }

verify_dependencies() {
    local missing_deps=()
    for cmd in kubectl helm ansible ansible-playbook jq curl; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_deps+=("$cmd")
        fi
    done
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "FATAL: Missing required system dependencies: ${missing_deps[*]}"
        log_warn "Please ensure they are installed and available in your system PATH before continuing."
        exit 1
    fi
}

backup_config() {
    mkdir -p "$BACKUP_DIR"
    cp -r "$REPO_ROOT/ansible" "$BACKUP_DIR/" 2>/dev/null || true
    log_info "Configuration snapshot archive generated at: $BACKUP_DIR"
}

main() {
    verify_dependencies
    backup_config
    log_warn "Running CEI Labs Repair Diagnostics..."

    # Check 1: Existence of all.yml
    if [[ ! -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
        log_error "Missing all.yml"
        read -p "Create from example configuration template? (y/n): " CREATE
        if [[ "${CREATE,,}" == "y" ]]; then
            cp "$REPO_ROOT/ansible/group_vars/all.yml.example" "$REPO_ROOT/ansible/group_vars/all.yml"
            log_info "Instantiated initial all.yml configuration variable file."
        fi
    fi

    # Check 2: Configuration key checks and unreplaced placeholder values
    if [[ -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
        # Expanded placeholder matching to detect additional defaults, example tags, and structural strings
        if grep -qi "<your-org>\|<changeme>\|TODO\|CHANGE_ME_BEFORE_EVERY_EVENT\|CHANGE_ME_SECURE_DASHBOARD_PASSWORD\|YOUR_REGISTRY_DOMAIN\|<your-username>\|<your-password>" "$REPO_ROOT/ansible/group_vars/all.yml"; then
            log_warn "Warning: Unreplaced placeholder credentials or organization strings found in all.yml."
        fi
        
        # Verify valid baseline infrastructure orchestrator configuration settings
        if ! grep -q "k3s_version" "$REPO_ROOT/ansible/group_vars/all.yml"; then
            log_error "Critical cluster configuration parameter definition missing: k3s_version"
        fi
    fi

    # Check 3: Check basic inventory layout file
    if [[ ! -f "$REPO_ROOT/ansible/inventory.ini" ]]; then
        log_error "Missing required orchestration ledger: ansible/inventory.ini"
    fi

    log_info "Diagnostic scan process complete. Check log file records for target vectors."
}

main "$@"