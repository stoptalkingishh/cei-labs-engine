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
echo "  CTF Platform — Deployment"
echo "═══════════════════════════════════════════════════"

# ── 1. Namespaces ─────────────────────────────────────────────────────────────
echo ""
echo "── Step 1: Namespaces ──────────────────────────────"
run "kubectl apply -f k8s/namespaces/namespaces.yml"

# ── 2. Traefik ingress controller ────────────────────────────────────────────
echo ""
echo "── Step 2: Traefik v3 ingress ──────────────────────"
run "helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true"
run "helm repo update"
run "helm upgrade --install traefik traefik/traefik \
  --namespace traefik \
  --create-namespace \
  --set nodeSelector.'node-role\.kubernetes\.io/master'=true \
  --set service.type=LoadBalancer \
  --wait"

# ── 3. CTFd stack ────────────────────────────────────────────────────────────
echo ""
echo "── Step 3: CTFd (MariaDB + Redis + CTFd) ───────────"
run "kubectl apply -f k8s/ctfd/ctfd-deployment.yml -n ctfd"
run "kubectl rollout status statefulset/ctfd-db -n ctfd --timeout=120s"
run "kubectl rollout status deployment/ctfd-redis -n ctfd --timeout=60s"
run "kubectl rollout status deployment/ctfd -n ctfd --timeout=120s"
echo "    CTFd is running at http://ctfd.ctf.local"
echo "    Complete setup wizard on first visit to set admin account."

# ── 4. MultiJuicer ───────────────────────────────────────────────────────────
echo ""
echo "── Step 4: MultiJuicer (OWASP Juice Shop) ──────────"
run "helm upgrade --install multi-juicer \
  oci://ghcr.io/juice-shop/multi-juicer/helm/multi-juicer \
  -f k8s/multijuicer/values.yml \
  --namespace multijuicer \
  --create-namespace \
  --wait"
echo "    MultiJuicer admin panel: http://juiceshop.ctf.local/balancer/admin"

# ── 5. Traefik ingress routes ────────────────────────────────────────────────
echo ""
echo "── Step 5: Ingress routes ──────────────────────────"
run "kubectl apply -f k8s/ingress/traefik-ingress.yml"

# ── 6. Juice Shop CTFd import ────────────────────────────────────────────────
echo ""
echo "── Step 6: Juice Shop CTFd import ──────────────────"
echo "    Run separately after CTFd admin account is created:"
echo "    ./scripts/juice-shop-ctf-import.sh"

# ── 7. Load CTFd challenges ──────────────────────────────────────────────────
echo ""
echo "── Step 7: Load CTFd challenge definitions ──────────"
echo "    Run separately after CTFd admin token is set in group_vars/all.yml:"
echo "    ./scripts/challenges-load.sh"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Platform deployment complete."
echo ""
echo "  Access points (add to /etc/hosts or local DNS):"
echo "    ctfd.ctf.local       → CTFd scoring platform"
echo "    juiceshop.ctf.local  → MultiJuicer (Juice Shop)"
echo ""
echo "  Next steps:"
echo "    1. Visit http://ctfd.ctf.local — complete setup wizard"
echo "    2. Create admin account, note the API token"
echo "    3. Update ctfd_admin_token in ansible/group_vars/all.yml"
echo "    4. Run ./scripts/juice-shop-ctf-import.sh"
echo "    5. Run ./scripts/challenges-load.sh"
echo "    6. Run ./scripts/spawn-analysts.sh roster.txt"
echo "═══════════════════════════════════════════════════"
