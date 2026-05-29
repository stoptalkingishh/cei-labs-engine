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

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

CTFD_URL="${CTFD_URL:-http://ctfd.ctf.local}"
CTFD_ADMIN_TOKEN="${CTFD_ADMIN_TOKEN:-}"
SPRINT="all"
DRY_RUN=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# ── FIXED 1: Hardened While-Loop Positional Argument Parser ───────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sprint)
      if [[ -z "${2:-}" || "${2:-}" == -* ]]; then
        log_error "Error: --sprint requires a valid tier parameter (1, 2, 3, or all)."
        exit 1
      fi
      SPRINT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      log_error "Unknown validation parameter passed: $1"
      echo "Usage: $0 [--sprint 1|2|3|all] [--dry-run]"
      exit 1
      ;;
  esac
done

if [[ -z "$CTFD_ADMIN_TOKEN" ]]; then
  log_warn "CTFD_ADMIN_TOKEN variable is empty."
  read -rsp "Enter CTFd Admin Personal Access Token: " CTFD_ADMIN_TOKEN
  echo
fi

# ── Ensure ctfcli toolchain dependencies are available ────────────────────────
if ! command -v ctf &> /dev/null; then
  log_info "Target ctfcli tool missing. Initiating quiet pip installation..."
  pip install ctfcli --quiet
fi

# ── Configure global execution mapping ────────────────────────────────────────
mkdir -p ~/.config/ctfcli
cat > ~/.config/ctfcli/config.yml << EOF
url: ${CTFD_URL}
access_token: ${CTFD_ADMIN_TOKEN}
EOF
log_info "ctfcli target connection baseline updated for: ${CTFD_URL}"

# ── Load Challenges Processing Logic ──────────────────────────────────────────
load_sprint() {
  local dir="$1"
  local label="$2"
  
  echo ""
  echo "── Loading ${label} ─────────────────────────────────"
  
  if [[ ! -d "$dir" ]]; then
    log_warn "Directory root target '${dir}' does not exist yet. Skipping..."
    return 0
  fi

  # FIXED 2: Leverages find print0 to guarantee clean file mapping even if targets are empty
  while IFS= read -r -d '' yml; do
    [[ -f "$yml" ]] || continue
    
    local base_file
    base_file=$(basename "$yml")
    local parent_dir
    parent_dir=$(dirname "$yml")
    
    log_info "Processing manifest specification: ${base_file}"
    
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "    [DRY-RUN] (cd ${parent_dir} && ctf challenge push ${base_file})"
    else
      # FIXED 3: Subshell context change ensures ctfcli cleanly links relative asset payloads
      (
        cd "$parent_dir"
        if ! ctf challenge push "$base_file" &>/dev/null; then
          # Attempt a fallback sync if the challenge was already populated once (Idempotency)
          if ! ctf challenge sync "$base_file" &>/dev/null; then
            log_error "Failed to synchronize challenge: ${base_file}. Review local engine or CTFd logs."
          fi
        fi
      )
    fi
  done < <(find "$dir" -maxdepth 1 -type f \( -name "*.yml" -o -name "*.yaml" \) -print0 2>/dev/null)
}

# ── Run Target Phase Routing Matrix ───────────────────────────────────────────
case "$SPRINT" in
  1|"sprint1")
    load_sprint "challenges/sprint1-otw" "Sprint 1 (OTW Bandit + CmdChallenge)"
    ;;
  2|"sprint2")
    load_sprint "challenges/sprint2-web" "Sprint 2 (Natas, Krypton, Leviathan, Juice Shop, PCAP)"
    ;;
  3|"sprint3")
    load_sprint "challenges/sprint3-pccc" "Sprint 3 (Narnia, Behemoth, crAPI, PCCC)"
    ;;
  all|*)
    load_sprint "challenges/sprint1-otw" "Sprint 1 — Linux Essentials"
    load_sprint "challenges/sprint2-web" "Sprint 2 — Web Application Security"
    load_sprint "challenges/sprint3-pccc" "Sprint 3 — Advanced Binary & Architecture Labs"
    ;;
esac

echo ""
log_info "Challenge load pipeline execution run complete."
echo "     Verify ingested assets directly at: ${CTFD_URL}/admin/challenges"
echo ""
log_warn "Reminder: Configure verification gates in the CTFd administration board if necessary."