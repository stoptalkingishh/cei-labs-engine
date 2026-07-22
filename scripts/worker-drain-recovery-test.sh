#!/usr/bin/env bash
# Real-Swarm worker drain/recovery acceptance test.
set -euo pipefail

SERVICE_NAME="${WORKER_DRAIN_TEST_SERVICE:-ceilabs-worker-drain-test}"
TEST_IMAGE="${WORKER_DRAIN_TEST_IMAGE:-alpine:3.20}"
TIMEOUT_SECONDS="${WORKER_DRAIN_TEST_TIMEOUT_SECONDS:-120}"
POLL_SECONDS=2

affected_node=""
service_created=false

cleanup() {
    exit_code=$?
    trap - EXIT INT TERM

    if [[ -n "$affected_node" ]]; then
        availability=$(docker node inspect "$affected_node" --format '{{.Spec.Availability}}' 2>/dev/null || true)
        if [[ "$availability" == "drain" ]]; then
            docker node update --availability active "$affected_node" >/dev/null || true
        fi
    fi
    if [[ "$service_created" == "true" ]]; then
        docker service rm "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_running_node() {
    excluded_node="${1:-}"
    deadline=$((SECONDS + TIMEOUT_SECONDS))
    while (( SECONDS < deadline )); do
        node=$(docker service ps "$SERVICE_NAME" \
            --filter desired-state=running \
            --format '{{.Node}}|{{.CurrentState}}' \
            | awk -F'|' -v excluded="$excluded_node" '$2 ~ /^Running / && $1 != excluded { print $1; exit }')
        if [[ -n "$node" ]]; then
            printf '%s\n' "$node"
            return 0
        fi
        sleep "$POLL_SECONDS"
    done
    return 1
}

if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep -qx true; then
    echo "ERROR: run this test from a Swarm manager" >&2
    exit 1
fi

mapfile -t active_workers < <(
    docker node ls --filter role=worker --format '{{.Hostname}}|{{.Status}}|{{.Availability}}' \
        | awk -F'|' '$2 == "Ready" && $3 == "Active" { print $1 }'
)
if (( ${#active_workers[@]} < 2 )); then
    echo "ERROR: at least two Ready/Active worker nodes are required for migration; found ${#active_workers[@]}" >&2
    exit 1
fi
if docker service inspect "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "ERROR: test service already exists: $SERVICE_NAME" >&2
    exit 1
fi

echo "+ docker node ls"
docker node ls

echo "+ docker service create --name $SERVICE_NAME --constraint node.role==worker $TEST_IMAGE"
docker service create \
    --detach=true \
    --name "$SERVICE_NAME" \
    --constraint 'node.role==worker' \
    --restart-condition any \
    "$TEST_IMAGE" sh -c 'while :; do sleep 3600; done' >/dev/null
service_created=true

if ! affected_node=$(wait_for_running_node); then
    echo "ERROR: test service did not become Running within ${TIMEOUT_SECONDS}s" >&2
    docker service ps "$SERVICE_NAME" --no-trunc >&2 || true
    exit 1
fi
echo "Initial task is running on worker: $affected_node"

echo "+ docker node update --availability drain $affected_node"
docker node update --availability drain "$affected_node"

if ! recovery_node=$(wait_for_running_node "$affected_node"); then
    echo "ERROR: service did not migrate away from $affected_node within ${TIMEOUT_SECONDS}s" >&2
    docker service ps "$SERVICE_NAME" --no-trunc >&2 || true
    exit 1
fi

echo "+ docker service ps $SERVICE_NAME"
docker service ps "$SERVICE_NAME"

echo "+ docker node update --availability active $affected_node"
docker node update --availability active "$affected_node"

availability=$(docker node inspect "$affected_node" --format '{{.Spec.Availability}}')
if [[ "$availability" != "active" ]]; then
    echo "ERROR: $affected_node availability is $availability after restore" >&2
    exit 1
fi
if [[ "$recovery_node" == "$affected_node" ]]; then
    echo "ERROR: task never migrated to another worker" >&2
    exit 1
fi

echo "PASS: $SERVICE_NAME migrated from $affected_node to $recovery_node and $affected_node was restored Active"
