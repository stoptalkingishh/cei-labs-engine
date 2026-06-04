#!/usr/bin/env bash
# scripts/repair.sh - Diagnostic & Repair Tool
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$HOME/cei-backups/repair_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$REPO_ROOT/repair.log"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log() { echo -e "$1" | tee -a "$LOG_FILE"; }

backup_config() {
    mkdir -p "$BACKUP_DIR"
    cp -r "$REPO_ROOT/ansible" "$BACKUP_DIR/" 2>/dev/null || true
    log "${GREEN}Backup created at: $BACKUP_DIR${NC}"
}

main() {
    backup_config
    echo -e "${YELLOW}Running CEI Labs Repair Diagnostics...${NC}"

    # Check 1: Existence of all.yml
    if [[ ! -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
        echo -e "${RED}Missing all.yml${NC}"
        read -p "Create from example? (y/n): " CREATE
        [[ $CREATE == "y" ]] && cp "$REPO_ROOT/ansible/group_vars/all.yml.example" "$REPO_ROOT/ansible/group_vars/all.yml"
    fi

    # Check 2: Configuration key checks and unreplaced placeholder values
    if [[ -f "$REPO_ROOT/ansible/group_vars/all.yml" ]]; then
        if grep -q "<your-org>\|<changeme>\|TODO" "$REPO_ROOT/ansible/group_vars/all.yml"; then
            echo -e "${YELLOW}[!] Warning: Unreplaced placeholder credentials or organization strings found in all.yml.${NC}"
        fi
        
        # Verify basic expected infrastructure components exist inside configuration
        for key in "k3s_version" "ctfd_version" "multijuicer_version"; do
            if ! grep -q "$key" "$REPO_ROOT/ansible/group_vars/all.yml"; then
                echo -e "${RED}[!] Critical configuration parameter missing: $key${NC}"
            fi
        done
    fi

    # Check 3: Check basic inventory layout file
    if [[ ! -f "$REPO_ROOT/ansible/inventory.ini" ]]; then
        echo -e "${RED}[!] Missing ansible/inventory.ini file.${NC}"
    fi

    echo -e "${GREEN}Repair scan complete. Check logs for details.${NC}"
}

main "$@"