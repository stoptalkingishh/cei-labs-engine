#!/usr/bin/env bash
# scripts/setup-cases.sh
set -euo pipefail

TARGET_DIR="/opt/ctf-cases"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]{NC} $*"; }
log_error() { echo -e "${RED}[-]{NC} $*" >&2; }

log_info "Preparing host forensic workspace at: ${TARGET_DIR}"
sudo mkdir -p "${TARGET_DIR}"
sudo chmod 755 "${TARGET_DIR}"

if [ ! -f "${TARGET_DIR}/evidence.pcap" ]; then
  log_warn "Creating structural baseline case template file..."
  echo "BASE_CASE_EMPTY_FRAMEWORK" | sudo tee "${TARGET_DIR}/evidence.pcap" > /dev/null
fi

log_info "Workspace verified. Drop forensic PCAP evidence files here before starting analyst instances."