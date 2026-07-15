#!/usr/bin/env bash
# Timestamped, machine-readable host and Docker telemetry for load tests.
# Usage: ./scripts/capture-resources.sh [output-directory]

set -euo pipefail

INTERVAL_SECONDS="${CEI_MONITOR_INTERVAL_SECONDS:-5}"
RUN_ID="${CEI_MONITOR_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUTPUT_DIR="${1:-evidence/resources-${RUN_ID}}"

if ! [[ "$INTERVAL_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "CEI_MONITOR_INTERVAL_SECONDS must be a positive integer" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
TAB=$'\t'

printf 'run_id\t%s\nstarted_utc\t%s\nhostname\t%s\ninterval_seconds\t%s\n' \
  "$RUN_ID" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname)" "$INTERVAL_SECONDS" \
  > "$OUTPUT_DIR/manifest.tsv"
docker_server_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
swarm_state=$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || true)
swarm_nodes=$(docker info --format '{{.Swarm.Nodes}}' 2>/dev/null || true)
swarm_managers=$(docker info --format '{{.Swarm.Managers}}' 2>/dev/null || true)
printf 'docker_server_version\t%s\nswarm_state\t%s\nswarm_nodes\t%s\nswarm_managers\t%s\n' \
  "$docker_server_version" "$swarm_state" "$swarm_nodes" "$swarm_managers" \
  >> "$OUTPUT_DIR/manifest.tsv"

printf 'timestamp_utc\tload_1m\tload_5m\tload_15m\tmem_total_bytes\tmem_used_bytes\tmem_available_bytes\tswap_total_bytes\tswap_used_bytes\troot_total_bytes\troot_used_bytes\n' \
  > "$OUTPUT_DIR/host.tsv"
printf 'timestamp_utc\tinterface\trx_bytes\trx_packets\trx_errors\trx_dropped\ttx_bytes\ttx_packets\ttx_errors\ttx_dropped\n' \
  > "$OUTPUT_DIR/network.tsv"
printf 'timestamp_utc\tcontainer_id\tname\tcpu_percent\tmemory_usage\tmemory_percent\tnetwork_io\tblock_io\tpids\n' \
  > "$OUTPUT_DIR/containers.tsv"
printf 'timestamp_utc\tname\tmode\treplicas\timage\tports\n' > "$OUTPUT_DIR/services.tsv"

docker events --since "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --format '{{json .}}' > "$OUTPUT_DIR/docker-events.jsonl" 2>&1 &
EVENTS_PID=$!
FINISHED=0

finish() {
  if [[ "$FINISHED" -eq 1 ]]; then
    return
  fi
  FINISHED=1
  kill "$EVENTS_PID" 2>/dev/null || true
  wait "$EVENTS_PID" 2>/dev/null || true
  printf 'finished_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$OUTPUT_DIR/manifest.tsv"
  docker service ls --format '{{json .}}' > "$OUTPUT_DIR/services-final.jsonl" 2>&1 || true
  docker ps --no-trunc --format '{{json .}}' > "$OUTPUT_DIR/containers-final.jsonl" 2>&1 || true
  echo "Resource evidence written to $OUTPUT_DIR"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while true; do
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  read -r load_1m load_5m load_15m _ < /proc/loadavg
  read -r mem_total mem_used _ _ _ mem_available < <(free -b | awk '/^Mem:/ {print $2, $3, $4, $5, $6, $7}')
  read -r swap_total swap_used _ < <(free -b | awk '/^Swap:/ {print $2, $3, $4}')
  read -r root_total root_used _ < <(df -B1 --output=size,used,avail / | tail -n 1)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$timestamp" "$load_1m" "$load_5m" "$load_15m" "$mem_total" "$mem_used" "$mem_available" \
    "$swap_total" "$swap_used" "$root_total" "$root_used" >> "$OUTPUT_DIR/host.tsv"

  awk -v ts="$timestamp" 'NR > 2 && $1 != "lo:" {gsub(":", "", $1); print ts "\t" $1 "\t" $2 "\t" $3 "\t" $4 "\t" $5 "\t" $10 "\t" $11 "\t" $12 "\t" $13}' \
    /proc/net/dev >> "$OUTPUT_DIR/network.tsv"

  docker stats --no-stream \
    --format "${timestamp}${TAB}{{.ID}}${TAB}{{.Name}}${TAB}{{.CPUPerc}}${TAB}{{.MemUsage}}${TAB}{{.MemPerc}}${TAB}{{.NetIO}}${TAB}{{.BlockIO}}${TAB}{{.PIDs}}" \
    >> "$OUTPUT_DIR/containers.tsv" 2>&1 || true
  docker service ls --format "${timestamp}${TAB}{{.Name}}${TAB}{{.Mode}}${TAB}{{.Replicas}}${TAB}{{.Image}}${TAB}{{.Ports}}" \
    >> "$OUTPUT_DIR/services.tsv" 2>&1 || true

  sleep "$INTERVAL_SECONDS"
done
