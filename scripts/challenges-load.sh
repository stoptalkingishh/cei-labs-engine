#!/usr/bin/env bash
# scripts/challenges-load.sh
# Loads all CTFd challenge definitions from YAML files using ctfcli.
# Run after CTFd is up and the admin token is set.
#
# ctfcli docs: https://github.com/CTFd/ctfcli
#
# Usage:
#   export CTFD_URL=http://ctfd.ctf.local
#   export CTFD_ADMIN_TOKEN=your-admin-token
#   ./scripts/challenges-load.sh
#   ./scripts/challenges-load.sh --sprint 1    # load only Sprint 1
#   ./scripts/challenges-load.sh --dry-run

set -euo pipefail

CTFD_URL="${CTFD_URL:-http://ctfd.ctf.local}"
CTFD_ADMIN_TOKEN="${CTFD_ADMIN_TOKEN:-}"
SPRINT="${SPRINT:-all}"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --sprint) SPRINT="${2:-all}"; shift ;;
    --dry-run) DRY_RUN=true ;;
  esac
done

if [[ -z "$CTFD_ADMIN_TOKEN" ]]; then
  read -rsp "CTFd admin token: " CTFD_ADMIN_TOKEN
  echo
fi

# ── Ensure ctfcli is installed ────────────────────────────────────────────────
if ! command -v ctf &> /dev/null; then
  echo "[+] Installing ctfcli..."
  pip install ctfcli --quiet
fi

# ── Configure ctfcli ──────────────────────────────────────────────────────────
mkdir -p ~/.config/ctfcli
cat > ~/.config/ctfcli/config.yml << EOF
url: ${CTFD_URL}
access_token: ${CTFD_ADMIN_TOKEN}
EOF
echo "[+] ctfcli configured for ${CTFD_URL}"

# ── Load challenges ───────────────────────────────────────────────────────────
load_sprint() {
  local dir="$1"
  local label="$2"
  echo ""
  echo "── Loading ${label} ─────────────────────────────────"
  for yml in "${dir}"/*.yml; do
    [[ -f "$yml" ]] || continue
    echo "    Processing: $(basename "$yml")"
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "    [DRY-RUN] ctf challenge push ${yml}"
    else
      ctf challenge push "${yml}" || {
        echo "    [WARN] Failed to push $(basename "$yml") — check CTFd logs"
      }
    fi
  done
}

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
    load_sprint "challenges/sprint1-otw" "Sprint 1"
    load_sprint "challenges/sprint2-web" "Sprint 2"
    load_sprint "challenges/sprint3-pccc" "Sprint 3"
    ;;
esac

echo ""
echo "[+] Challenge load complete."
echo "    Verify at: ${CTFD_URL}/admin/challenges"
echo ""
echo "    Reminder: CTFd prerequisites (gating) must be configured"
echo "    in the CTFd admin panel — ctfcli YAML supports requirements"
echo "    but verify the gate chain is correct after loading:"
echo "      Sprint 1 gate: Bandit 14->15 solved → unlocks Sprint 2"
echo "      Sprint 2 gate: Bandit complete + 25 Juice Shop + 5 PCAP → unlocks Sprint 3"
