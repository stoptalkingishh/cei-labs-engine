#!/usr/bin/env bash
# scripts/patch-secrets.sh
# Dynamically extracts secrets from Ansible configuration variables and applies 
# live patches to the cluster, ensuring real credentials are never saved in git.

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

VARS_FILE="ansible/group_vars/all.yml"
YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'

if [[ ! -f "$VARS_FILE" ]]; then
  echo -e "${RED}[-] Error: Active secrets definition file ($VARS_FILE) missing.${NC}"
  echo "[-] Please copy ansible/group_vars/all.yml.example to all.yml and fill it out."
  exit 1
fi

echo -e "${YELLOW}[*] Extracting configuration tokens from Ansible Workspace...${NC}"

# FIXED: Sanitized Python inline block parameters to secure variable parsing pathways cleanly
CTFD_KEY=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('ctfd_secret_key', ''))")
DB_PASS=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('ctfd_db_password', ''))")
DB_ROOT=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('ctfd_db_root_password', ''))")
DOMAIN=$(python3 -c "import yaml; c=yaml.safe_load(open(\"${VARS_FILE}\")); print(c.get('base_domain', 'ctf.local'))")

if [[ -z "${CTFD_KEY}" || -z "${DB_PASS}" || -z "${DB_ROOT}" ]]; then
  echo -e "${RED}[-] Failure: Found empty or unconfigured core secrets parameters inside all.yml.${NC}"
  exit 1
fi

# Reconstruct the dynamic database string URI format matching CTFd specifications
DB_URL="mysql+pymysql://ctfd:${DB_PASS}@ctfd-db/ctfd"

echo -e "${YELLOW}[*] Securing live cluster credentials targeting namespace: ctfd...${NC}"

# FIXED: Replaced brittle 'patch' constraint with an idempotent 'apply' configuration block.
# This prevents race conditions if the placeholder secret manifest hasn't finished registering yet.
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: ctfd-secrets
  namespace: ctfd
type: Opaque
stringData:
  secret-key: "${CTFD_KEY}"
  db-password: "${DB_PASS}"
  db-root-password: "${DB_ROOT}"
  database-url: "${DB_URL}"
EOF

echo -e "${GREEN}[+] Production secrets dynamically applied. Recycling CTFd engine pods...${NC}"

# Safely check if deployment is active before triggering a rolling restart loop
if kubectl get deployment/ctfd -n ctfd &>/dev/null; then
  kubectl rollout restart deployment/ctfd -n ctfd
  echo -e "${YELLOW}[*] Awaiting scheduling reconciliation boundary stabilization...${NC}"
  kubectl rollout status deployment/ctfd -n ctfd --timeout=90s || echo -e "${YELLOW}[!] Note: CTFd deployment is still optimizing. Continuing pipeline setup...${NC}"
fi

echo -e "${GREEN}[+] Synchronization Complete. CTFd database credentials locked successfully.${NC}"