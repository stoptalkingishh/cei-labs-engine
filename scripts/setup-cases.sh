#!/usr/bin/env bash
# scripts/setup-cases.sh
set -euo pipefail

TARGET_DIR="/opt/ctf-cases"
echo "[+] Preparing host forensic workspace at: ${TARGET_DIR}"
sudo mkdir -p "${TARGET_DIR}"
sudo chmod 755 "${TARGET_DIR}"

if [ ! -f "${TARGET_DIR}/evidence.pcap" ]; then
  echo "[*] Creating structural baseline case template file..."
  echo "BASE_CASE_EMPTY_FRAMEWORK" | sudo tee "${TARGET_DIR}/evidence.pcap" > /dev/null
fi
echo "[+] Workspace verified. Drop forensic PCAP evidence files here before starting analyst instances."