#!/usr/bin/env bash
# scripts/challenges-load.sh
# Loads all CTFd challenge definitions from YAML files using ctfcli cleanly.
# Run after CTFd is up and the admin token is set.
#
# ctfcli docs: https://github.com/CTFd/ctfcli
#
# Usage:
#   export CTFD_URL=http://ctfd.ctf.local
#   export CTFD_ADMIN_TOKEN=your-admin-token
#   ./scripts/challenges-load.sh
#   ./scripts/challenges-load.sh --sprint 1
#   ./scripts/challenges-load.sh --dry-run

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CTFD_URL="${CTFD_URL:-https://ctfd.ctf.local}"
CTFD_ADMIN_TOKEN="${CTFD_ADMIN_TOKEN:-}"
SYNC_SECRET_FILE="${SYNC_SECRET_FILE:-$REPO_ROOT/docker/secrets/plugin_shared_secret.txt}"
SPRINT="all"
DRY_RUN=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# ── HARDENED ARGUMENT PARSER ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--sprint)
      if [[ -n "${2:-}" && ! "$2" =~ ^- ]]; then
        SPRINT="$2"
        shift 2
      else
        log_error "Error: --sprint requires a valid choice argument [1, 2, 3, or all]."
        exit 1
      fi
      ;;
    -d|--dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--sprint <1|2|3|all>] [--dry-run]"
      exit 0
      ;;
    *)
      log_error "Unknown validation parameter passed: $1"
      exit 1
      ;;
  esac
done

# ── ENVIRONMENT PRE-FLIGHT VERIFICATION ───────────────────────────────────────
if [[ "$DRY_RUN" == "false" ]]; then
  if [[ -z "${CTFD_ADMIN_TOKEN}" ]]; then
    log_error "Missing administrative target validation parameters!"
    echo "Please set your access token: export CTFD_ADMIN_TOKEN=your_token"
    exit 1
  fi

  if ! command -v ctf &>/dev/null; then
    log_error "The 'ctfcli' binary could not be located in the current environment path."
    echo "Please activate your virtual environment or install via: pip install ctfcli"
    exit 1
  fi

  # FIXED: Verify presence of active local ctfcli workspace before running commands
  if [[ ! -d ".ctf" ]]; then
    log_warn "Local ctfcli workspace registry directory not detected. Initializing context..."
    printf '%s\n%s\n' "${CTFD_URL}" "${CTFD_ADMIN_TOKEN}" | ctf init >/dev/null || true
  fi
fi

# ── INSTANCE MAPPING SYNC ──────────────────────────────────────────────────────
# Optional: a challenge.yml may declare an `instance_type` (and the images/
# ports it needs — see docker/orchestrator/README.md) for challenges that
# should offer a "Launch Environment" link. Pushes that mapping into the
# CTFd instance-launcher plugin so the link works. Silently does nothing for
# ordinary challenges that don't declare instance_type.
sync_instance_mapping() {
  local challenge_path="$1"

  if [[ ! -f "$SYNC_SECRET_FILE" ]]; then
    return 0
  fi

  local payload
  payload=$(python3 - "$challenge_path" <<'PYEOF' 2>/dev/null || true
import json
import sys

import yaml

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f) or {}

instance_type = data.get("instance_type")
if not instance_type:
    sys.exit(0)

print(json.dumps({
    "challenge_name": data.get("name"),
    "instance_type": instance_type,
    "image": data.get("image"),
    "port": data.get("port"),
    "target_image": data.get("target_image"),
    "attacker_image": data.get("attacker_image"),
    "attacker_port": data.get("attacker_port"),
}))
PYEOF
  )

  [[ -z "$payload" ]] && return 0

  local sync_secret
  sync_secret=$(cat "$SYNC_SECRET_FILE")
  local http_code
  http_code=$(curl -sk -o /dev/null -w '%{http_code}' \
    -X POST "${CTFD_URL}/plugins/instance-launcher/admin/mappings/sync" \
    -H "X-Sync-Auth: ${sync_secret}" \
    -H "Content-Type: application/json" \
    -d "$payload")

  if [[ "$http_code" == "200" ]]; then
    log_info "Synced instance mapping for $(basename "$challenge_path")"
  else
    log_warn "Instance mapping sync returned HTTP ${http_code} for $(basename "$challenge_path")"
  fi
}

# ── CHALLENGE INGESTION HANDLER ───────────────────────────────────────────────
load_sprint() {
  local dir="$1"
  local description="$2"

  if [[ ! -d "$dir" ]]; then
    log_warn "Directory reference '$dir' missing from tree. Skipping deployment of ${description}..."
    return 0
  fi

  log_info "Synchronizing Workspace Stack: ${description} [${dir}]"

  # FIXED: Wrapped stream iterations cleanly inside a secure find -print0 pipeline
  while IFS= read -r -d '' challenge_path; do
    [[ -z "$challenge_path" ]] && continue

    local parent_dir
    local base_file
    parent_dir=$(dirname "$challenge_path")
    base_file=$(basename "$challenge_path")

    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY-RUN] Would synchronize asset layout config: ${challenge_path}"
    else
      log_info "Ingesting Challenge Target Manifest ──> ${base_file}"
      
      # FIXED: Wrapped directory paths in double quotes to prevent unescaped word splitting
      (
        cd "$parent_dir"
        if ! ctf challenge push "$base_file" &>/dev/null; then
          # Fallback sync optimization if the target challenge entry exists already
          if ! ctf challenge sync "$base_file" &>/dev/null; then
            log_error "Failed to synchronize challenge manifest: ${base_file}. Review local deployment state."
          fi
        fi
      )
      sync_instance_mapping "$challenge_path"
    fi
  done < <(find "$dir" -maxdepth 1 -type f \( -name "*.yml" -o -name "*.yaml" \) -print0 2>/dev/null)
}

# ── ROUTE INGESTION MATRIX ────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════"
echo "  CEI Labs Engine — Challenge Sync Utility"
echo "═══════════════════════════════════════════════════"

case "$SPRINT" in
  1|"sprint1")
    load_sprint "challenges/sprint1-otw" "Sprint 1 (OTW Bandit + CmdChallenge)"
    ;;
  2|"sprint2")
    load_sprint "challenges/sprint2-web" "Sprint 2 (Natas, Krypton, Juice Shop, PCAP)"
    ;;
  3|"sprint3")
    load_sprint "challenges/sprint3-pccc" "Sprint 3 (Narnia, Behemoth, crAPI, PCCC)"
    ;;
  all|*)
    load_sprint "challenges/sprint1-otw" "Sprint 1 — Linux Essentials"
    load_sprint "challenges/sprint2-web" "Sprint 2 — Web Application Security"
    load_sprint "challenges/sprint3-pccc" "Sprint 3 — Binary Exploitation & PCCC Core"
    ;;
esac

log_info "Challenge ingestion sync phase operations executed successfully."