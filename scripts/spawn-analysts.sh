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

# FIXED: Dynamically extract configuration values directly from Ansible group_vars
if [[ -f "$VARS_FILE" ]]; then
  BASE_PORT=$(python3 -c "import yaml; c=yaml.safe_load(open('$VARS_FILE')); print(c.get('analyst_base_port', $DEFAULT_PORT))" 2>/dev/null || echo "$DEFAULT_PORT")
  TAG=$(python3 -c "import yaml; c=yaml.safe_load(open('$VARS_FILE')); print(c.get('analyst_image_tag', '$DEFAULT_TAG'))" 2>/dev/null || echo "$DEFAULT_TAG")
  ORG=$(python3 -c "import yaml; c=yaml.safe_load(open('$VARS_FILE')); print(c.get('github_org', '$DEFAULT_ORG'))" 2>/dev/null || echo "$DEFAULT_ORG")
else
  BASE_PORT=$DEFAULT_PORT
  TAG=$DEFAULT_TAG
  ORG=$DEFAULT_ORG
fi

NAMESPACE="analyst"
# FIXED (Item 6): Realigned the target GHCR path layout to cleanly include the platform repository layer 
IMAGE="ghcr.io/${ORG}/cei-labs-engine/ctf-analyst:${TAG}"
CREDS_FILE="creds.txt"

# Hardened Control-Plane IP Parsing Engine
BASTION_IP="${BASTION_IP:-}"
if [[ -z "$BASTION_IP" ]]; then
  BASTION_IP=$(kubectl get nodes -l node-role.kubernetes.io/control-plane=true -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "127.0.0.1")
fi

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log_info()   { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

# Initialize Namespace Bound
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

TEARDOWN=false
STATUS=false
ROSTER=""

# Standardized While-Loop Positional Argument Parser
while [[ $# -gt 0 ]]; do
  case "$1" in
    --teardown)
      TEARDOWN=true
      shift
      ;;
    --status)
      STATUS=true
      shift
      ;;
    -*)
      log_error "Unknown validation parameter passed: $1"
      echo "Usage: $0 [--teardown] [--status] [roster.txt]"
      exit 1
      ;;
    *)
      ROSTER="$1"
      shift
      ;;
  esac
done

# Execute Actions Based on Evaluated State Flags
if [[ "$TEARDOWN" == "true" ]]; then
  log_warn "Removing all active analyst pods and routing rules..."
  kubectl delete pods -n "${NAMESPACE}" -l "app=analyst" --grace-period=5 --ignore-not-found=true
  kubectl delete services -n "${NAMESPACE}" -l "app=analyst" --ignore-not-found=true
  log_info "All analyst components evicted cleanly."
  exit 0
fi

if [[ "$STATUS" == "true" ]]; then
  echo "=== Analyst Workspaces Running Status ==="
  kubectl get pods -n "${NAMESPACE}" -l "app=analyst" -o wide
  echo ""
  echo "=== Live NodePort Port Assignment Mapping ==="
  kubectl get services -n "${NAMESPACE}" -l "app=analyst"
  exit 0
fi

if [[ -z "$ROSTER" || ! -f "$ROSTER" ]]; then
  log_error "Error: Missing valid participant roster target file."
  echo "Usage: $0 [roster.txt]"
  exit 1
fi

# Initialize Clean Output Matrix
> "${CREDS_FILE}"
echo "# CTF Event Credentials — $(date)" >> "${CREDS_FILE}"
echo "# Bastion IP: ${BASTION_IP}" >> "${CREDS_FILE}"
echo "# Format: username | port | password | ssh_command" >> "${CREDS_FILE}"
echo "" >> "${CREDS_FILE}"

PORT=$BASE_PORT

while IFS= read -r username || [[ -n "$username" ]]; do
  [[ -z "$username" || "$username" == \#* ]] && continue
  username=$(echo "${username}" | tr -d '\r' | xargs)
  [[ -z "$username" ]] && continue

  PASSWORD=$(tr -dc 'a-km-np-zA-HJ-NP-Z2-9' </dev/urandom | head -c 12)
  POD_NAME="analyst-${username}"
  SVC_NAME="analyst-svc-${username}"

  # Create Individual Analyst Workspace Pods
  kubectl apply -n "${NAMESPACE}" -f - << EOF
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
      # Added Startup Probe to guarantee SSH readiness detection before passing traffic
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
  kubectl apply -n "${NAMESPACE}" -f - << EOF
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
  printf "%-20s | port %-5s | pass %-14s | %s\n" \
    "${username}" "${PORT}" "${PASSWORD}" "${SSH_CMD}" >> "${CREDS_FILE}"
  echo -e "${GREEN}[+]${NC} ${username} → port ${YELLOW}${PORT}${NC}"

  PORT=$(( PORT + 1 ))
done < "${ROSTER}"

echo ""
log_info "Credentials compiled successfully into: ${CREDS_FILE}"