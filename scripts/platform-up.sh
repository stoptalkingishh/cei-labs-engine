#!/usr/bin/env bash
# scripts/platform-up.sh
# Deploys all CTF platform services to the K3s cluster in dependency order.
# Run this after ansible/site.yml has provisioned all nodes.
#
# Usage:
#   ./scripts/platform-up.sh
#   ./scripts/platform-up.sh --skip-images   # skip image pre-pull
#   ./scripts/platform-up.sh --dry-run       # show commands without running

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

DRY_RUN=false
SKIP_IMAGES=false
for arg in "$@"; do
  [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
  [[ "$arg" == "--skip-images" ]] && SKIP_IMAGES=true
done

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] $*"
  else
    echo "[+] $*"
    eval "$*"
  fi
}

echo "═══════════════════════════════════════════════════"
echo "  CEI Labs Engine — Deployment Matrix"
echo "═══════════════════════════════════════════════════"

# ── 1. Namespaces ─────────────────────────────────────────────────────────────
echo ""
echo "── Step 1: Namespaces ──────────────────────────────"
run "kubectl apply -f k8s/namespaces/namespaces.yml"

# ── 2. Local Container Registry ───────────────────────────────────────────────
echo ""
echo "── Step 2: Internal Air-Gap Image Registry ─────────"
if [ -f "k8s/registry/registry.yml" ]; then
  run "kubectl apply -f k8s/registry/registry.yml"
  run "kubectl rollout status deployment/local-registry -n registry --timeout=60s"
else
  echo "[!] Local registry configuration sheet missing. Skipping deployment..."
fi

# ── 3. Traefik ingress controller ────────────────────────────────────────────
echo ""
echo "── Step 3: Traefik v3 Ingress Core ─────────────────"
run "helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true"
run "helm repo update"

# FIXED: Swapped out legacy master label injection for the canonical control-plane value file
if [ -f "k8s/ingress/traefik-values.yml" ]; then
  run "helm upgrade --install traefik traefik/traefik \
    --namespace traefik \
    -f k8s/ingress/traefik-values.yml \
    --wait"
else
  run "helm upgrade --install traefik traefik/traefik \
    --namespace traefik \
    --set nodeSelector.'node-role\.kubernetes\.io/control-plane'=true \
    --set service.type=LoadBalancer \
    --wait"
fi

# ── 4. CTFd stack ────────────────────────────────────────────────────────────
echo ""
echo "── Step 4: CTFd Core Ecosystem (MariaDB + Redis) ───"
run "kubectl apply -f k8s/ctfd/ctfd-deployment.yml"
run "kubectl rollout status statefulset/ctfd-db -n ctfd --timeout=120s"
run "kubectl rollout status deployment/ctfd-redis -n ctfd --timeout=60s"
run "kubectl rollout status deployment/ctfd -n ctfd --timeout=120s"
echo "    CTFd score server initialized at http://ctfd.ctf.local"

# ── 5. MultiJuicer ───────────────────────────────────────────────────────────
echo ""
echo "── Step 5: MultiJuicer Component Cluster ────────────"
# FIXED: Name switched from multi-juicer to multijuicer to align with platform-down.sh purge hooks
run "helm upgrade --install multijuicer \
  oci://ghcr.io/juice-shop/multi-juicer/helm/multi-juicer \
  -f k8s/multijuicer/values.yml \
  --namespace multijuicer \
  --wait"
echo "    MultiJuicer gate running at: http://juiceshop.ctf.local/balancer/admin"

# ── 6. Traefik ingress routes ────────────────────────────────────────────────
echo ""
echo "── Step 6: Ingress Routing Topology ────────────────"
run "kubectl apply -f k8s/ingress/traefik-ingress.yml"

# ── 7. Juice Shop CTFd import ────────────────────────────────────────────────
echo ""
echo "── Step 7: Juice Shop CTFd import ──────────────────"
echo "    Run separately after CTFd admin account is created:"
echo "    ./scripts/juice-shop-ctf-import.sh"

# ── 8. Load CTFd challenges ──────────────────────────────────────────────────
echo ""
echo "── Step 8: Load Challenge Specifications ───────────"
echo "    Run separately after CTFd admin token is set in group_vars/all.yml:"
echo "    ./scripts/challenges-load.sh"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Platform deployment complete."
echo ""
echo "  Access points (add to /etc/hosts or local DNS):"
echo "    ctfd.ctf.local       → CTFd scoring platform"
echo "    juiceshop.ctf.local  → MultiJuicer (Juice Shop)"
echo "    registry.ctf.local   → Internal Storage Hub"
echo ""
echo "  Next steps:"
echo "    1. Visit http://ctfd.ctf.local — complete setup wizard"
echo "    2. Create admin account, note the API token"
echo "    3. Update ctfd_admin_token in ansible/group_vars/all.yml"
echo "    4. Run ./scripts/juice-shop-ctf-import.sh"
echo "    5. Run ./scripts/challenges-load.sh"
echo "    6. Run ./scripts/spawn-analysts.sh roster.txt"
echo "═══════════════════════════════════════════════════"