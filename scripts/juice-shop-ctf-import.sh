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

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# ── Validate inputs ───────────────────────────────────────────────────────────
if [[ -z "$CTFD_ADMIN_TOKEN" ]]; then
  read -rsp "CTFd admin token: " CTFD_ADMIN_TOKEN
  echo
fi

if [[ -z "$CTF_KEY" ]]; then
  read -rsp "CTF key (must match juiceshop_ctf_key in values.yml): " CTF_KEY
  echo
fi

log_info "Waiting for Juice Shop to be reachable at ${JUICESHOP_URL}..."
for i in $(seq 1 20); do
  if curl -sf "${JUICESHOP_URL}/api/Challenges" > /dev/null 2>&1; then
    log_info "Juice Shop is up."
    break
  fi
  echo "    Attempt ${i}/20 — waiting 10s..."
  sleep 10
done

log_info "Running juice-shop-ctf-cli..."
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

log_info "Generated import file: ${IMPORT_FILE}"

log_info "Importing into CTFd at ${CTFD_URL}..."
curl -s -X POST \
  "${CTFD_URL}/api/v1/import" \
  -H "Authorization: Token ${CTFD_ADMIN_TOKEN}" \
  -H "Content-Type: multipart/form-data" \
  -F "backup=@${IMPORT_FILE};type=application/zip" \
  | jq '.'

echo ""
log_info "Juice Shop challenges imported into CTFd."
echo "    Verify at: ${CTFD_URL}/admin/challenges"
echo ""
log_warn "IMPORTANT: All Juice Shop flags are seeded from CTF_KEY."
log_warn "If you change the key in values.yml, re-run this script."