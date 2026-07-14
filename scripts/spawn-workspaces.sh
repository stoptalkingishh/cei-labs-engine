#!/usr/bin/env bash
# scripts/spawn-workspaces.sh
# Bulk, admin-driven provisioning of per-participant Docker Swarm services —
# replaces scripts/spawn-analysts.sh (kubectl Pod+NodePort Service per user).
#
# This is the roster-driven bulk path for pre-event provisioning (e.g. an
# entire cohort's SSH analyst boxes ahead of a training track). Participant
# self-service, on-demand instances (Juice Shop, target+attacker wargames)
# go through the Challenge Instance Orchestrator + CTFd instead — see
# docker/orchestrator/README.md.
#
# Usage:
#   ./scripts/spawn-workspaces.sh roster.txt [--type analyst|kali]
#   ./scripts/spawn-workspaces.sh --teardown [--type analyst|kali]
#   ./scripts/spawn-workspaces.sh --status   [--type analyst|kali]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/docker/.env"

DEFAULT_BASE_PORT=30001
DEFAULT_TAG="latest"
DEFAULT_ORG="your-github-org"
DEFAULT_TYPE="analyst"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# ── Config ────────────────────────────────────────────────────────────────────
BASE_PORT="$DEFAULT_BASE_PORT"
TAG="$DEFAULT_TAG"
ORG="$DEFAULT_ORG"

if [[ -f "$ENV_FILE" ]]; then
  # docker/.env is plain KEY=value, safe to source directly (same file
  # `docker stack deploy` itself reads for variable interpolation).
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  BASE_PORT="${ANALYST_BASE_PORT:-$DEFAULT_BASE_PORT}"
  TAG="${IMAGE_TAG:-$DEFAULT_TAG}"
  ORG="${GITHUB_ORG:-$DEFAULT_ORG}"
else
  log_warn "docker/.env not found — using built-in defaults. Copy docker/.env.example to docker/.env to configure."
fi

TYPE="$DEFAULT_TYPE"
MODE=""
ROSTER=""

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --teardown) MODE="teardown"; shift ;;
    --status)   MODE="status"; shift ;;
    --type)
      TYPE="${2:-}"
      if [[ "$TYPE" != "analyst" && "$TYPE" != "kali" ]]; then
        log_error "Error: --type must be 'analyst' or 'kali'."
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [roster.txt | --teardown | --status] [--type analyst|kali]"
      exit 0
      ;;
    *)
      if [[ -z "$MODE" && -z "$ROSTER" ]]; then
        ROSTER="$1"
        shift
      else
        log_error "Unknown argument: $1"
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$MODE" && -z "$ROSTER" ]]; then
  log_error "Missing operational argument."
  echo "Usage: $0 [roster.txt | --teardown | --status] [--type analyst|kali]"
  exit 1
fi

case "$TYPE" in
  analyst) IMAGE="ghcr.io/${ORG}/cei-labs-engine/ctf-analyst:${TAG}"; TARGET_PORT=22 ;;
  kali)    IMAGE="ghcr.io/${ORG}/cei-labs-engine/ctf-kali-novnc:${TAG}"; TARGET_PORT=6080 ;;
esac

LABEL="app=workspace"
TYPE_LABEL="workspace-type=${TYPE}"

# ── Placement: prefer worker nodes; fall back gracefully on single-node/eval
# setups where the only node in the swarm is a manager (no --constraint at
# all in that case, so it still schedules).
placement_args() {
  local worker_count
  worker_count=$(docker node ls --filter role=worker -q 2>/dev/null | wc -l | tr -d '[:space:]')
  if [[ "${worker_count:-0}" -gt 0 ]]; then
    echo "--constraint" "node.role==worker"
  fi
}

# ── Teardown ──────────────────────────────────────────────────────────────────
teardown_all() {
  log_warn "Removing all ${TYPE} workspace services..."
  local ids
  ids=$(docker service ls --filter "label=${LABEL}" --filter "label=${TYPE_LABEL}" -q)
  if [[ -z "$ids" ]]; then
    log_info "No ${TYPE} workspaces currently running."
    return 0
  fi
  echo "$ids" | xargs -r docker service rm
  log_info "Cleanup complete."
}

# ── Status ────────────────────────────────────────────────────────────────────
show_status() {
  echo "═════════════════════════════════════════════════════════════════════"
  echo "   Active Workspaces — ${TYPE}"
  echo "═════════════════════════════════════════════════════════════════════"
  printf "%-25s | %-8s | %-30s\n" "Service" "Replicas" "Published Port"
  echo "─────────────────────────────────────────────────────────────────────"
  docker service ls --filter "label=${LABEL}" --filter "label=${TYPE_LABEL}" \
    --format '{{.Name}} {{.Replicas}} {{.Ports}}' |
    while read -r name replicas ports; do
      printf "%-25s | %-8s | %-30s\n" "$name" "$replicas" "${ports:-N/A}"
    done
  echo "═════════════════════════════════════════════════════════════════════"
}

if [[ "$MODE" == "teardown" ]]; then
  teardown_all
  exit 0
elif [[ "$MODE" == "status" ]]; then
  show_status
  exit 0
fi

if [[ ! -f "$ROSTER" ]]; then
  log_error "Roster file not found: $ROSTER"
  exit 1
fi

mapfile -t PLACEMENT_ARGS < <(placement_args)

COUNTER=0
echo "═════════════════════════════════════════════════════════════════════"
printf "%-20s | %-10s | %-15s\n" "Username" "Port" "Generated Pass"
echo "─────────────────────────────────────────────────────────────────────"

while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" =~ ^# ]] && continue

  username=$(echo "$line" | tr -d '\r' | tr -d ' ' | tr '[:upper:]' '[:lower:]')
  [[ -z "$username" ]] && continue

  PORT=$((BASE_PORT + COUNTER))
  COUNTER=$((COUNTER + 1))

  SERVICE_NAME="workspace-${TYPE}-${username}"

  if docker service inspect "$SERVICE_NAME" >/dev/null 2>&1; then
    log_warn "Service ${SERVICE_NAME} already exists — skipping (use --teardown first to recreate)."
    continue
  fi

  # `head -c 14` closing early SIGPIPEs the upstream `tr`, which pipefail
  # treats as pipeline failure — the previous `|| echo "C3iLabsSecret1!"`
  # fallback here did NOT replace the output on failure, it *appended* to
  # it (command substitution captures stdout from both sides of `||`),
  # so every generated password silently ended in the same publicly-known
  # literal string regardless of the "random" prefix. Verified: 5/5 test
  # runs produced "<14 random chars>C3iLabsSecret1!". The subshell + `||
  # true` below absorbs the pipefail failure without emitting anything.
  PASSWORD=$( (LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 14) || true )

  CREATE_ARGS=(
    docker service create --detach
    --name "$SERVICE_NAME"
    --label "$LABEL"
    --label "$TYPE_LABEL"
    --label "participant=${username}"
    --publish "published=${PORT},target=${TARGET_PORT}"
    --limit-memory 512m
    --reserve-memory 128m
    --restart-condition on-failure
  )
  CREATE_ARGS+=("${PLACEMENT_ARGS[@]}")

  if [[ "$TYPE" == "analyst" ]]; then
    CREATE_ARGS+=(--env "OPERATOR_PASSWORD=${PASSWORD}")
    CREATE_ARGS+=(--mount "type=bind,source=/opt/ctf-cases,target=/home/operator/cases,readonly")
  else
    CREATE_ARGS+=(--env "VNC_PASSWORD=${PASSWORD}")
  fi

  CREATE_ARGS+=("$IMAGE")

  "${CREATE_ARGS[@]}" >/dev/null

  printf "%-20s | %-10s | %-15s\n" "${username}" "${PORT}" "${PASSWORD}"
done < "$ROSTER"

echo "═════════════════════════════════════════════════════════════════════"
log_info "Workspaces provisioned. Connect to any swarm node's IP on the listed port"
log_info "(Swarm's routing mesh reaches the right container regardless of which node it landed on)."
