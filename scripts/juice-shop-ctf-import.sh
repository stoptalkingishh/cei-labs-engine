#!/usr/bin/env bash
# scripts/juice-shop-ctf-import.sh
# Runs juice-shop-ctf-cli against a throwaway Juice Shop instance, generates
# a CTFd import ZIP, and imports it via the CTFd admin API.
#
# There is no longer a single standing "juiceshop.ctf.local" to point this
# at — Juice Shop instances are now created on demand, per team, by the
# Challenge Instance Orchestrator (docker/orchestrator). So this script asks
# the orchestrator directly for a throwaway instance (owner_id=admin-import),
# uses it to generate the flag-import file, then tears it down.
#
# The orchestrator is deliberately not exposed outside the swarm, so this
# script reaches it the same way the CTFd plugin does: a short-lived
# container attached to the orchestrator-internal overlay network. Run this
# from a machine with `docker` access to the swarm (a manager, or over
# `DOCKER_HOST`/SSH to one).
#
# Prerequisites:
#   - Node.js installed on this machine (npx available)
#   - The CEI Labs stack is deployed (./scripts/stack-up.sh)
#   - docker/secrets/plugin_shared_secret.txt and docker/secrets/ctf_key.txt exist
#
# Usage:
#   export CTFD_URL=https://ctfd.ctf.local
#   export CTFD_ADMIN_TOKEN=your-admin-token
#   ./scripts/juice-shop-ctf-import.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"

CTFD_URL="${CTFD_URL:-https://ctfd.ctf.local}"
CTFD_ADMIN_TOKEN="${CTFD_ADMIN_TOKEN:-}"
JUICE_SHOP_IMAGE="${JUICE_SHOP_IMAGE:-bkimminich/juice-shop:latest}"
ORCHESTRATOR_NETWORK="${ORCHESTRATOR_NETWORK:-cei-labs_orchestrator-internal}"
IMPORT_FILE="/tmp/juice-shop-ctfd-import.zip"
OWNER_ID="admin-import"
INSTANCE_KEY="juice-shop-import"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

if [[ -z "$CTFD_ADMIN_TOKEN" ]]; then
  read -rsp "CTFd admin token: " CTFD_ADMIN_TOKEN
  echo
fi

if [[ ! -f "$DOCKER_DIR/secrets/plugin_shared_secret.txt" || ! -f "$DOCKER_DIR/secrets/ctf_key.txt" ]]; then
  log_error "docker/secrets/plugin_shared_secret.txt and ctf_key.txt are required (see docker/secrets.example/)."
  exit 1
fi
PLUGIN_SECRET=$(cat "$DOCKER_DIR/secrets/plugin_shared_secret.txt")
CTF_KEY=$(cat "$DOCKER_DIR/secrets/ctf_key.txt")

orchestrator_call() {
  # $1=method $2=path $3=json body (optional)
  docker run --rm --network "$ORCHESTRATOR_NETWORK" curlimages/curl:latest \
    -s -X "$1" "http://orchestrator:8080$2" \
    -H "X-Orchestrator-Auth: ${PLUGIN_SECRET}" \
    -H "Content-Type: application/json" \
    ${3:+-d "$3"}
}

log_info "Requesting a throwaway Juice Shop instance from the orchestrator..."
BODY=$(cat <<JSON
{"type": "web-app", "owner_id": "${OWNER_ID}", "instance_key": "${INSTANCE_KEY}", "spec": {"image": "${JUICE_SHOP_IMAGE}", "port": 3000, "env": {"CTF_KEY": "${CTF_KEY}"}}}
JSON
)
RESPONSE=$(orchestrator_call POST /instances "$BODY")
JUICESHOP_URL=$(echo "$RESPONSE" | jq -r '.access.url // empty')

if [[ -z "$JUICESHOP_URL" ]]; then
  log_error "Orchestrator did not return an access URL. Response: $RESPONSE"
  exit 1
fi
log_info "Throwaway instance: ${JUICESHOP_URL}"

cleanup() {
  log_warn "Tearing down throwaway instance..."
  orchestrator_call DELETE "/instances/${OWNER_ID}/${INSTANCE_KEY}" >/dev/null || true
}
trap cleanup EXIT

log_info "Waiting for Juice Shop to be reachable..."
for i in $(seq 1 20); do
  if curl -sfk "${JUICESHOP_URL}/api/Challenges" > /dev/null 2>&1; then
    log_info "Juice Shop is up."
    break
  fi
  echo "    Attempt ${i}/20 — waiting 10s..."
  sleep 10
done

log_info "Running juice-shop-ctf-cli..."
npx juice-shop-ctf-cli << EOF
ctfd
${JUICESHOP_URL}
${CTF_KEY}
zip
${IMPORT_FILE}
EOF

log_info "Generated import file: ${IMPORT_FILE}"

log_info "Importing into CTFd at ${CTFD_URL}..."
curl -sk -X POST \
  "${CTFD_URL}/api/v1/import" \
  -H "Authorization: Token ${CTFD_ADMIN_TOKEN}" \
  -H "Content-Type: multipart/form-data" \
  -F "backup=@${IMPORT_FILE};type=application/zip" \
  | jq '.'

echo ""
log_info "Juice Shop challenges imported into CTFd."
echo "    Verify at: ${CTFD_URL}/admin/challenges"
echo ""
log_warn "IMPORTANT: All Juice Shop flags are seeded from ctf_key. If you rotate"
log_warn "docker/secrets/ctf_key.txt, re-run this script."
