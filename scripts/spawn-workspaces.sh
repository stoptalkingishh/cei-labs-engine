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
# First port of the orchestrator's own published SSH range (32000-32767,
# ORCHESTRATOR_SSH_PORT_RANGE_START/END in docker/.env.example). Workspaces
# must stay strictly below it: a collision publishes a challenge instance's
# SSH on a port an analyst workspace already owns, `docker service create`
# fails with "Bind for 0.0.0.0:32200 failed: port is already allocated", and
# the challenge never starts — surfacing much later as an opaque orchestrator
# timeout with nothing pointing at the port. The default matches
# .env.example; override with ORCHESTRATOR_SSH_PORT_RANGE_START (or
# WORKSPACE_PORT_CEILING) if the orchestrator range is configured elsewhere.
DEFAULT_ORCHESTRATOR_PORT_RANGE_START=32000

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# ── Config ────────────────────────────────────────────────────────────────────
# ANALYST_BASE_PORT is read from the environment here as well as from
# docker/.env below. It used to be honoured only inside the `if .env exists`
# branch, so on a station with no .env (or to reposition a roster from the
# command line) the override was silently dropped and every run started at
# 30001 — which is exactly the knob you need to reach when a roster no longer
# fits below the orchestrator port range.
BASE_PORT="${ANALYST_BASE_PORT:-$DEFAULT_BASE_PORT}"
TAG="$DEFAULT_TAG"
ORG="$DEFAULT_ORG"
# Seed the ceiling from the environment before docker/.env is sourced, so a
# station with no .env still honours ORCHESTRATOR_SSH_PORT_RANGE_START (or the
# WORKSPACE_PORT_CEILING alias). If .env *does* define the range, that value
# wins below — deliberately, because .env is what the orchestrator itself
# reads, so letting a shell variable override it here would compute a ceiling
# the orchestrator doesn't agree with and reintroduce the collision.
PORT_CEILING="${ORCHESTRATOR_SSH_PORT_RANGE_START:-${WORKSPACE_PORT_CEILING:-$DEFAULT_ORCHESTRATOR_PORT_RANGE_START}}"

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
  # .env is the authoritative place ORCHESTRATOR_SSH_PORT_RANGE_START lives
  # (the orchestrator reads the same file), so it overrides the seed above.
  PORT_CEILING="${ORCHESTRATOR_SSH_PORT_RANGE_START:-$PORT_CEILING}"
else
  log_warn "docker/.env not found — using built-in defaults. Copy docker/.env.example to docker/.env to configure."
fi

# Validated only after .env is sourced, because that is where BASE_PORT and
# the orchestrator range actually come from — validating earlier would check
# values that are about to be overwritten.
if ! [[ "$PORT_CEILING" =~ ^[0-9]+$ ]] || [[ "$PORT_CEILING" -le 0 ]]; then
  log_error "Error: ORCHESTRATOR_SSH_PORT_RANGE_START must be a positive integer (got '${PORT_CEILING}')."
  exit 1
fi
if ! [[ "$BASE_PORT" =~ ^[0-9]+$ ]] || [[ "$BASE_PORT" -ge "$PORT_CEILING" ]]; then
  log_error "Error: ANALYST_BASE_PORT (${BASE_PORT}) must be a number below ORCHESTRATOR_SSH_PORT_RANGE_START (${PORT_CEILING}). Workspace ports would collide with the orchestrator's own 32000-32767 SSH range."
  exit 1
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

# ── Capability hardening ─────────────────────────────────────────────────────
# Mirrors docker/orchestrator/app/instance_types.py's _ATTACKER_CAPS exactly.
# Without this, plain `docker service create` runs with Docker's full default
# capability set -- combined with operator/analyst/Dockerfile's `operator
# ALL=(ALL) NOPASSWD: ALL` sudoers entry, that's a much easier path to
# root/container-escape than the orchestrator-provisioned equivalent (which
# has run cap_drop=["ALL"] plus this same narrow cap_add since Phase 6)
# allows. Both `analyst` and `kali` images here are the same "attacker
# workstation" role as instance_types.py's plan_range_attacker: SSH/sudo
# (SYS_CHROOT for sshd's privilege-separation preauth child, SETUID/SETGID/
# SETFCAP/SETPCAP for chpasswd/su-style per-connection privilege drops) plus
# NET_RAW/NET_ADMIN for nmap/tcpdump packet capture.
ATTACKER_CAPS=(
  CHOWN DAC_OVERRIDE FOWNER FSETID KILL
  SETGID SETUID SETFCAP SETPCAP
  NET_BIND_SERVICE AUDIT_WRITE SYS_CHROOT
  NET_RAW NET_ADMIN
)

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
ROSTER_LINE=0
echo "═════════════════════════════════════════════════════════════════════"
printf "%-20s | %-10s | %-15s\n" "Username" "Port" "Generated Pass"
echo "─────────────────────────────────────────────────────────────────────"

while IFS= read -r line || [[ -n "$line" ]]; do
  ROSTER_LINE=$((ROSTER_LINE + 1))
  [[ -z "$line" || "$line" =~ ^# ]] && continue

  username=$(echo "$line" | tr -d '\r' | tr -d ' ' | tr '[:upper:]' '[:lower:]')
  [[ -z "$username" ]] && continue

  PORT=$((BASE_PORT + COUNTER))

  # Nothing used to bound PORT, so a ~2000-line roster walked straight out of
  # the workspace band and into the orchestrator's 32000-32767 SSH range.
  # Fail before creating anything, and name the roster line so the operator
  # can drop the offending entry instead of bisecting a 2000-line file.
  if [[ "$PORT" -ge "$PORT_CEILING" ]]; then
    log_error "Roster line ${ROSTER_LINE} ('${username}') would need published port ${PORT}, which is inside the orchestrator's SSH range (${PORT_CEILING}-32767)."
    log_error "Refusing to continue: only $((PORT_CEILING - BASE_PORT)) workspace(s) fit between ANALYST_BASE_PORT=${BASE_PORT} and the ceiling."
    log_error "Fix by shrinking the roster, splitting it into a second roster with a lower ANALYST_BASE_PORT, or raising ORCHESTRATOR_SSH_PORT_RANGE_START. (docker/.env.example: keep ANALYST_BASE_PORT + roster size below ${PORT_CEILING}.)"
    exit 1
  fi

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
    --cap-drop ALL
  )
  for cap in "${ATTACKER_CAPS[@]}"; do
    CREATE_ARGS+=(--cap-add "$cap")
  done
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
