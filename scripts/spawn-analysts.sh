#!/usr/bin/env bash
# scripts/spawn-analysts.sh
set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

# FIXED (Item 3): Isolated back to the dedicated analyst tracking workspace
NAMESPACE="analyst"
IMAGE="ghcr.io/${GITHUB_ORG:-your-org}/ctf-platform/ctf-analyst:latest"
BASE_PORT=2201
CREDS_FILE="creds.txt"

BASTION_IP="${BASTION_IP:-}"
if [[ -z "$BASTION_IP" ]]; then
  BASTION_IP=$(kubectl get nodes -o wide | grep "control-plane" | awk '{print $6}' || true)
  if [[ -z "$BASTION_IP" ]]; then
    BASTION_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "127.0.0.1")
  fi
fi

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

if [[ "${1:-}" == "--teardown" ]]; then
  echo -e "${YELLOW}[!] Removing all analyst pods...${NC}"
  kubectl delete pods -n "${NAMESPACE}" -l "app=analyst" --grace-period=5 --ignore-not-found=true
  kubectl delete services -n "${NAMESPACE}" -l "app=analyst" --ignore-not-found=true
  echo -e "${GREEN}[+] All analyst pods removed.${NC}"
  exit 0
fi

if [[ "${1:-}" == "--status" ]]; then
  echo "Analyst pods:"
  kubectl get pods -n "${NAMESPACE}" -l "app=analyst" -o wide
  echo ""
  echo "Analyst services (SSH ports):"
  kubectl get services -n "${NAMESPACE}" -l "app=analyst"
  exit 0
fi

ROSTER="${1:-}"
if [[ -z "$ROSTER" || ! -f "$ROSTER" ]]; then
  echo "Usage: $0 [--teardown|--status] <roster.txt>"
  exit 1
fi

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
  # FIXED (Item 4): Targets the explicit custom key assigned by Ansible to Node 3
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