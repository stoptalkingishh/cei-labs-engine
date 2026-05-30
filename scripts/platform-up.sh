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

# Standard While-Loop Argument Parser
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --skip-images)
      SKIP_IMAGES=true
      shift
      ;;
    *)
      echo "[-] Unknown validation parameter passed: $1"
      echo "Usage: $0 [--dry-run] [--skip-images]"
      exit 1
      ;;
  esac
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
  # Points to the true, valid manifest deployment target name: local-registry
  run "kubectl rollout status deployment/local-registry -n registry --timeout=60s"
else
  echo "[!] Local registry configuration sheet missing. Skipping deployment..."
fi

# ── 2.5. Cert-Manager Core Setup (ADDED FOR PRODUCTION TLS) ───────────────────
echo ""
echo "── Step 2.5: Deploying Cert-Manager Core ──────────"
run "helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true"
run "helm repo update"
run "helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.14.4 \
  --set installCRDs=true \
  --wait"

if [ -f "k8s/ingress/cert-issuer.yml" ]; then
  run "kubectl apply -f k8s/ingress/cert-issuer.yml"
fi

# ── Image Pre-Pull Optimization Hook ─────────────────────────────────────────
if [[ "$SKIP_IMAGES" == "false" && "$DRY_RUN" == "false" ]]; then
  echo ""
  echo "── Optional Step: Core Infrastructure Image Pre-Pull ─"
  echo "[*] Optimizing worker caching nodes (Caching CTFd & DB engines)..."
  # Pre-cache heavy images to prevent rollout tracking from hitting timeouts later
  crictl pull docker.io/library/mariadb:10.11 2>/dev/null || true
  crictl pull docker.io/library/redis:7.0 2>/dev/null || true
fi

# ── 3. Traefik ingress controller ────────────────────────────────────────────
echo ""
echo "── Step 3: Traefik v3 Ingress Core ─────────────────"
run "helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true"
run "helm repo update"

# Realigned lookup string to point to the actual k8s/ingress/ path layout
if [ -f "k8s/ingress/traefik-values.yml" ]; then
  run "helm upgrade --install traefik traefik/traefik \
    --namespace traefik \
    --create-namespace \
    -f k8s/ingress/traefik-values.yml \
    --wait"
else
  run "helm upgrade --install traefik traefik/traefik \
    --namespace traefik \
    --create-namespace \
    --set nodeSelector.'node-role\.kubernetes\.io/control-plane'=true \
    --set service.type=LoadBalancer \
    --wait"
fi

# ── 4. CTFd stack ────────────────────────────────────────────────────────────
echo ""
echo "── Step 4: CTFd Core Ecosystem (MariaDB + Redis) ───"

# FIXED (Item 5): Moved secrets patching ahead of the manifest deployment.
# This injects real group_vars secrets before MariaDB initializes its storage directory.
if [[ -x "./scripts/patch-secrets.sh" && "$DRY_RUN" == "false" ]]; then
  echo "[*] Diverting to secure secrets injection pipeline..."
  ./scripts/patch-secrets.sh || echo "[!] Warning: Secrets patch execution bypassed or failed."
fi

run "kubectl apply -f k8s/ctfd/ctfd-deployment.yml"
run "kubectl rollout status statefulset/ctfd-db -n ctfd --timeout=120s"
run "kubectl rollout status deployment/ctfd-redis -n ctfd --timeout=60s"
run "kubectl rollout status deployment/ctfd -n ctfd --timeout=120s"
echo "    CTFd score server initialized at https://ctfd.ctf.local"

# ── 5. MultiJuicer ───────────────────────────────────────────────────────────
echo ""
echo "── Step 5: MultiJuicer Component Cluster ────────────"
run "helm upgrade --install multijuicer \
  oci://ghcr.io/juice-shop/multi-juicer/helm/multi-juicer \
  -f k8s/multijuicer/values.yml \
  --namespace multijuicer \
  --wait"
echo "    MultiJuicer gate running at: https://juiceshop.ctf.local/balancer/admin"

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
echo "    Run separately after CTFd admin token is generated:"
echo "    ./scripts/challenges-load.sh"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Platform deployment complete."
echo ""
echo "  Access points (add to /etc/hosts or local DNS):"
echo "    ctfd.ctf.local       → CTFd scoring platform (HTTPS)"
echo "    juiceshop.ctf.local  → MultiJuicer (Juice Shop) (HTTPS)"
echo "    registry.ctf.local   → Internal Storage Hub"
echo ""
echo "  Next steps:"
echo "    1. Visit https://ctfd.ctf.local — complete setup wizard"
echo "    2. Create admin account, note the API token"
echo "    3. Run ./scripts/challenges-load.sh"
# Eradicated Windows PowerShell execution instructions completely
echo "    4. Run ./scripts/spawn-analysts.sh roster.txt"
echo "═══════════════════════════════════════════════════"