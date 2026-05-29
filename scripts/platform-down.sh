#!/usr/bin/env bash
# scripts/platform-down.sh
# CEI Labs Platform Teardown Protocol
# Gracefully dismantles platform components across all operational namespaces.

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

echo -e "${YELLOW}[*] Initializing CEI Labs Graceful Infrastructure Shutdown...${NC}"

# 1. Clear ephemeral training environments and participant containers
echo "[*] Evicting runtime dynamic user components and analyst workspaces..."
# Cleans up across both historical tracking namespaces to guarantee no leakages
for ns in challenges analyst; do
  kubectl delete deployment --all -n "$ns" --ignore-not-found=true 2>/dev/null || true
  kubectl delete pods --all -n "$ns" -l "app=analyst" --grace-period=5 --ignore-not-found=true 2>/dev/null || true
  kubectl delete svc --all -n "$ns" --ignore-not-found=true 2>/dev/null || true
done

# 2. Safely dismantle MultiJuicer Helm Release
if command -v helm &>/dev/null; then
  # Check both potential namespace scopes to prevent next-run state conflicts
  for mj_ns in multijuicer apps; do
    if helm status multijuicer -n "$mj_ns" &>/dev/null; then
      echo "[*] Purging MultiJuicer Helm release from namespace: ${mj_ns}..."
      helm uninstall multijuicer -n "$mj_ns"
    fi
  done
else
  echo -e "${YELLOW}[!] Warning: Helm binary not found. Skipping Helm release purge.${NC}"
fi

# 3. Spin down core web apps but leave storage volume assertions intact
echo "[*] Stopping core platform engines..."

if [ -f "k8s/ingress/traefik-ingress.yml" ]; then
  kubectl delete -f k8s/ingress/traefik-ingress.yml --ignore-not-found=true
fi

# Tracks against the synchronized monolithic target we corrected
if [ -f "k8s/ctfd/ctfd-deployment.yml" ]; then
  kubectl delete -f k8s/ctfd/ctfd-deployment.yml --ignore-not-found=true
fi

echo ""
echo -e "${GREEN}[+] CEI Labs Engine Stopped. Persistence volumes preserved on Node 1.${NC}"