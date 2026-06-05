#!/usr/bin/env bash
# scripts/spawn-analysts.sh
# Creates one SSH-accessible analyst pod per participant.
# Pods are pinned to the analyst worker node via nodeSelector.
# Each pod gets a unique port mapping and random password.
#
# Usage:
#   ./scripts/spawn-analysts.sh roster.txt
#   ./scripts/spawn-analysts.sh --teardown
#   ./scripts/spawn-analysts.sh --status

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

VARS_FILE="ansible/group_vars/all.yml"
DEFAULT_PORT=30001
DEFAULT_TAG="latest"
DEFAULT_ORG="your-org"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# FIXED: Sanitized Python inline configurations to utilize escaped double-quotes for variable parsing
if [[ -f "$VARS_FILE" ]]; then
  BASE_PORT=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('analyst_base_port', ${DEFAULT_PORT}))" 2>/dev/null || echo "$DEFAULT_PORT")
  TAG=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('analyst_image_tag', \"${DEFAULT_TAG}\"))" 2>/dev/null || echo "$DEFAULT_TAG")
  ORG=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('github_org', \"${DEFAULT_ORG}\"))" 2>/dev/null || echo "$DEFAULT_ORG")
else
  BASE_PORT=$DEFAULT_PORT
  TAG=$DEFAULT_TAG
  ORG=$DEFAULT_ORG
fi

IMAGE="ghcr.io/${ORG}/cei-labs-engine/ctf-analyst:${TAG}"
NAMESPACE="analyst"

get_bastion_ip() {
  # Attempts to locate primary Control Plane node address string dynamically
  kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "127.0.0.1"
}

teardown_all() {
  log_warn "Initializing structural platform namespace wiping routine..."
  kubectl delete pods,services -n "${NAMESPACE}" -l app=analyst --timeout=60s || true
  log_info "Cleanup complete. Space cleared successfully."
}

show_status() {
  echo "═════════════════════════════════════════════════════════════════════"
  echo "   Active Labs — Dynamic Analyst Provisioning Matrix"
  echo "═════════════════════════════════════════════════════════════════════"
  printf "%-20s | %-12s | %-6s | %-10s\n" "Participant" "Pod Status" "Port" "IP Address"
  echo "─────────────────────────────────────────────────────────────────────"
  
  kubectl get pods -n "${NAMESPACE}" -l app=analyst -o json | jq -r '
    .items[] | 
    "\(.metadata.labels.participant) | \(.status.phase) | \(.spec.containers[0].ports[0].containerPort) | \(.status.podIP)"
  ' 2>/dev/null | while IFS=' | ' read -r user phase cport pip; do
    # Fetching corresponding nodeport translation parameters mapping safely
    sport=$(kubectl get svc -n "${NAMESPACE}" -l participant="${user}" -o jsonpath='{.items[0].spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
    printf "%-20s | %-12s | %-6s | %-10s\n" "${user}" "${phase}" "${sport}" "${pip}"
  done
  echo "═════════════════════════════════════════════════════════════════════"
}

# ── Route Logic Controller ───────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
  log_error "Missing operational argument criteria targets."
  echo "Usage: $0 [roster.txt | --teardown | --status]"
  exit 1
fi

if [[ "$1" == "--teardown" ]]; then
  teardown_all
  exit 0
elif [[ "$1" == "--status" ]]; then
  show_status
  exit 0
fi

ROSTER="$1"
if [[ ! -f "$ROSTER" ]]; then
  log_error "Specified lab target roster file ($ROSTER) could not be resolved."
  exit 1
fi

BASTION_IP=$(get_bastion_ip)
log_info "Utilizing target connectivity route gateway: ${BASTION_IP}"
log_warn "Initializing cloud infrastructure namespaces..."
kubectl create namespace "${NAMESPACE}" 2>/dev/null || true

COUNTER=0
echo "═════════════════════════════════════════════════════════════════════"
printf "%-20s | %-10s | %-15s | %-30s\n" "Username" "Port Map" "Generated Pass" "Direct Access String"
echo "─────────────────────────────────────────────────────────────────────"

# FIXED: Wrapped loop parameters to securely preserve trailing line entries cleanly
while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip comments or completely unpopulated line rows
  [[ -z "$line" || "$line" =~ ^# ]] && continue
  
  username=$(echo "$line" | tr -d '\r' | tr -d ' ' | tr '[:upper:]' '[:lower:]')
  [[ -z "$username" ]] && continue

  # FIXED: Extracted unneeded '$' tokens out of nested arithmetic expansion context
  PORT=$((BASE_PORT + COUNTER))
  COUNTER=$((COUNTER + 1))
  
  # Provision secure, cryptographically sound alpha-numeric credential tokens
  PASSWORD=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 14 || echo "C3iLabsSecret1!")
  
  POD_NAME="analyst-${username}"
  SVC_NAME="analyst-svc-${username}"

  # Provision Isolated Core Pod Configuration Instance
  cat <<EOF | kubectl apply -n "${NAMESPACE}" -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: analyst
    participant: "${username}"
spec:
  nodeSelector:
    role: analyst
  restartPolicy: Always
  containers:
    - name: analyst
      image: ${IMAGE}
      env:
        - name: OPERATOR_PASSWORD
          value: "${PASSWORD}"
      ports:
        - containerPort: 22
      startupProbe:
        tcpSocket:
          port: 22
        failureThreshold: 12
        periodSeconds: 5
      resources:
        requests:
          memory: "256Mi"
          cpu: "100m"
        limits:
          memory: "512Mi"
          cpu: "500m"
      volumeMounts:
        - name: cases
          mountPath: /home/operator/cases
          readOnly: true
  volumes:
    - name: cases
      hostPath:
        path: /opt/ctf-cases
        type: DirectoryOrCreate
EOF

  # Expose Dedicated Isolation NodePort Interface
  cat <<EOF | kubectl apply -n "${NAMESPACE}" -f - >/dev/null
apiVersion: v1
kind: Service
metadata:
  name: ${SVC_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: analyst
    participant: "${username}"
spec:
  type: NodePort
  selector:
    participant: "${username}"
  ports:
    - port: 22
      targetPort: 22
      nodePort: ${PORT}
EOF

  SSH_CMD="ssh operator@${BASTION_IP} -p ${PORT}"
  printf "%-20s | port %-5s | %-15s | %-30s\n" "${username}" "${PORT}" "${PASSWORD}" "${SSH_CMD}"

done < "$ROSTER"
echo "═════════════════════════════════════════════════════════════════════"
log_info "Active workspace environment configurations provisioned successfully."