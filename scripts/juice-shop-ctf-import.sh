#!/usr/bin/env bash
# scripts/juice-shop-ctf-import.sh
# Runs juice-shop-ctf-cli against the live MultiJuicer instance,
# generates a CTFd import ZIP, and imports it via the CTFd admin API.
#
# Prerequisites:
#   - Node.js installed on this machine (npx available)
#   - CTFd is running and admin token is set in environment or prompted
#   - MultiJuicer is running and at least one Juice Shop instance exists
#
# Usage:
#   export CTFD_URL=http://ctfd.ctf.local
#   export CTFD_ADMIN_TOKEN=your-admin-token
#   export CTF_KEY=CHANGE_ME_BEFORE_EVERY_EVENT   # must match values.yml
#   ./scripts/juice-shop-ctf-import.sh

set -euo pipefail

CTFD_URL="${CTFD_URL:-http://ctfd.ctf.local}"
CTFD_ADMIN_TOKEN="${CTFD_ADMIN_TOKEN:-}"
CTF_KEY="${CTF_KEY:-}"
JUICESHOP_URL="${JUICESHOP_URL:-http://juiceshop.ctf.local}"
IMPORT_FILE="/tmp/juice-shop-ctfd-import.zip"

# ── Validate inputs ───────────────────────────────────────────────────────────
if [[ -z "$CTFD_ADMIN_TOKEN" ]]; then
  read -rsp "CTFd admin token: " CTFD_ADMIN_TOKEN
  echo
fi

if [[ -z "$CTF_KEY" ]]; then
  read -rsp "CTF key (must match juiceshop_ctf_key in values.yml): " CTF_KEY
  echo
fi

echo "[+] Waiting for Juice Shop to be reachable at ${JUICESHOP_URL}..."
for i in $(seq 1 20); do
  if curl -sf "${JUICESHOP_URL}/api/Challenges" > /dev/null 2>&1; then
    echo "[+] Juice Shop is up."
    break
  fi
  echo "    Attempt ${i}/20 — waiting 10s..."
  sleep 10
done

echo "[+] Running juice-shop-ctf-cli..."
# juice-shop-ctf-cli prompts for:
#   - CTF framework: ctfd
#   - Juice Shop URL
#   - CTF key
#   - Output format: zip
# We pipe the answers in via heredoc
npx juice-shop-ctf-cli << EOF
ctfd
${JUICESHOP_URL}
${CTF_KEY}
zip
${IMPORT_FILE}
EOF

echo "[+] Generated import file: ${IMPORT_FILE}"

echo "[+] Importing into CTFd at ${CTFD_URL}..."
curl -s -X POST \
  "${CTFD_URL}/api/v1/import" \
  -H "Authorization: Token ${CTFD_ADMIN_TOKEN}" \
  -H "Content-Type: multipart/form-data" \
  -F "backup=@${IMPORT_FILE};type=application/zip" \
  | jq '.'

echo ""
echo "[+] Juice Shop challenges imported into CTFd."
echo "    Verify at: ${CTFD_URL}/admin/challenges"
echo ""
echo "    IMPORTANT: All Juice Shop flags are seeded from CTF_KEY."
echo "    If you change the key in values.yml, re-run this script."
