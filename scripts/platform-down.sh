#!/usr/bin/env bash
# scripts/platform-down.sh
# CEI Labs Platform Teardown Protocol
# Gracefully dismantles platform components across all operational namespaces.

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

echo -e "${YELLOW}[*] Initializing CEI Labs Graceful Infrastructure Shutdown...${NC}"

# ── 1. Clear Ephemeral Training Environments & Analyst Workspaces ─────────────
echo "[*] Evicting runtime dynamic user components and analyst workspaces..."
if [[ -x "./scripts/spawn-analysts.sh" ]]; then
  ./scripts/spawn-analysts.sh --teardown || true
else
  # Fallback manual loop protection if automation script is unreachable
  for ns in challenges analyst; do
    kubectl delete pods -n "$ns" -l "app=analyst" --grace-period=5 --ignore-not-found=true 2>/dev/null || true
    kubectl delete services -n "$ns" -l "app=analyst" --ignore-not-found=true 2>/dev/null || true
  done
fi

# ── 2. Safely Dismantle MultiJuicer & Traefik Helm Releases ───────────────────
if command -v helm &>/dev/null; then
  # Purge MultiJuicer lab releases
  for mj_ns in multijuicer apps; do
    if helm status multijuicer -n "$mj_ns" &>/dev/null; then
      echo "[*] Purging MultiJuicer Helm release from namespace: ${mj_ns}..."
      helm uninstall multijuicer -n "$mj_ns"
    fi
  done

  # FIXED (Item 9): Uninstalls Traefik release to allow clean external IP renewal cycles
  if helm status traefik -n traefik &>/dev/null; then
    echo "[*] Purging Traefik Ingress Helm release from namespace: traefik..."
    helm uninstall traefik -n traefik
  fi
else
  echo -e "${YELLOW}[!] Warning: Helm binary not found. Skipping Helm release purge cycles.${NC}"
fi

# ── 3. Dismantle Ingress Components & Local Infrastructure ────────────────────
echo "[*] Stopping core platform engines..."

if [ -f "k8s/ingress/traefik-ingress.yml" ]; then
  kubectl delete -f k8s/ingress/traefik-ingress.yml --ignore-not-found=true
fi

# Evict the complete CTFd application stack via kustomize
if [ -d "k8s/ctfd" ]; then
  kubectl delete -k k8s/ctfd/ --ignore-not-found=true
fi

# FIXED (Item 10): Removes the internal container registry to prevent runtime storage lock collisions
if [ -f "k8s/registry/registry.yml" ]; then
  echo "[*] Purging secure internal local container registry workspace components..."
  kubectl delete -f k8s/registry/registry.yml --ignore-not-found=true
fi

echo ""
echo -e "${GREEN}[+] CEI Labs Engine Stopped. Persistence volumes preserved on Node 1.${NC}"