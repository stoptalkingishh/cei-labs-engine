#!/usr/bin/env bash
# scripts/platform-up.sh
# Deploys all CTF platform services to the K3s cluster in dependency order.
# Run this after ansible/site.yml has provisioned all nodes.
#
# Usage:
#   ./scripts/platform-up.sh
#   ./scripts/platform-up.sh --dry-run        # show commands without running

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

DRY_RUN=false
VARS_FILE="ansible/group_vars/all.yml"

# Standard While-Loop Argument Parser
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      echo "[-] Unknown validation parameter passed: $1"
      echo "Usage: $0 [--dry-run]"
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
  run "kubectl rollout status deployment/local-registry -n registry --timeout=90s"
else
  echo "[!] Local registry configuration sheet missing. Skipping deployment..."
fi

# ── 3. Cert-Manager Core Setup ────────────────────────────────────────────────
echo ""
echo "── Step 3: Deploying Cert-Manager Core ──────────"
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

# ── 4. Traefik Ingress Controller ─────────────────────────────────────────────
echo ""
echo "── Step 4: Traefik v3 Ingress Core ─────────────────"
run "helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true"
run "helm repo update"

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
    --set service.type=LoadBalancer \
    --wait"
fi

# ── 5. CTFd Stack ─────────────────────────────────────────────────────────────
echo ""
echo "── Step 5: CTFd Core Ecosystem (MariaDB + Redis) ───"

# FIXED (B2): Place structural configuration definitions in-cluster FIRST
run "kubectl apply -f k8s/ctfd/ctfd-deployment.yml"

# FIXED (B2): Patch secrets pipeline instantly executes while containers are running init checks
if [[ -x "./scripts/patch-secrets.sh" && "$DRY_RUN" == "false" ]]; then
  echo "[*] Injecting secrets baseline values from group_vars..."
  ./scripts/patch-secrets.sh || echo "[!] Warning: Secrets patch execution failed."
fi

run "kubectl rollout status statefulset/ctfd-db -n ctfd --timeout=120s"
run "kubectl rollout status deployment/ctfd-redis -n ctfd --timeout=60s"
run "kubectl rollout status deployment/ctfd -n ctfd --timeout=120s"
echo "    CTFd score server initialized at http://ctfd.ctf.local"

# ── 6. MultiJuicer ────────────────────────────────────────────────────────────
echo ""
echo "── Step 6: MultiJuicer Component Cluster ────────────"

# FIXED (F5, B6): Dynamic group_vars data extraction block via Python one-liners
if [[ "$DRY_RUN" == "false" && -f "$VARS_FILE" ]]; then
  MAX_INSTANCES=$(python3 -c "import yaml; c=yaml.safe_load(open('$VARS_FILE')); print(c.get('multijuicer_max_instances', 30))" 2>/dev/null || echo "30")
  CTF_KEY=$(python3 -c "import yaml; c=yaml.safe_load(open('$VARS_FILE')); print(c.get('multijuicer_ctf_key', 'CHANGE_ME_BEFORE_EVERY_EVENT'))" 2>/dev/null || echo "CHANGE_ME_BEFORE_EVERY_EVENT")
  ADMIN_PASS=$(python3 -c "import yaml; c=yaml.safe_load(open('$VARS_FILE')); print(c.get('multijuicer_admin_password', 'CHANGE_ME_SECURE_DASHBOARD_PASSWORD'))" 2>/dev/null || echo "CHANGE_ME_SECURE_DASHBOARD_PASSWORD")
else
  MAX_INSTANCES=30
  CTF_KEY="CHANGE_ME_BEFORE_EVERY_EVENT"
  ADMIN_PASS="CHANGE_ME_SECURE_DASHBOARD_PASSWORD"
fi

run "helm upgrade --install multijuicer \
  oci://ghcr.io/juice-shop/multi-juicer/helm/multi-juicer \
  -f k8s/multijuicer/values.yml \
  --namespace multijuicer \
  --set juiceShop.maxInstances=${MAX_INSTANCES} \
  --set ctfKey=\"${CTF_KEY}\" \
  --set adminPassword=\"${ADMIN_PASS}\" \
  --wait"
echo "    MultiJuicer gate running at: http://juiceshop.ctf.local/balancer/admin"

# ── 7. Traefik Ingress Routes ─────────────────────────────────────────────────
echo ""
echo "── Step 7: Ingress Routing Topology ────────────────"
run "kubectl apply -f k8s/ingress/traefik-ingress.yml"

# ── 8. Automation Imports & Challenge Pipelines ──────────────────────────────
echo ""
echo "── Step 8: Post-Deployment Data Ingestion ──────────"
echo "    Run these manually sequentially after provisioning is complete:"
echo "      1. ./scripts/juice-shop-ctf-import.sh"
echo "      2. ./scripts/challenges-load.sh"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Platform deployment complete."
echo ""
echo "  Access points (add to /etc/hosts or local DNS):"
echo "    ctfd.ctf.local      → CTFd scoring platform"
echo "    juiceshop.ctf.local → MultiJuicer (Juice Shop)"
echo "    registry.ctf.local  → Internal Storage Hub"
echo ""
echo "  Execution Actions Required:"
echo "    1. Open http://ctfd.ctf.local — walk through setup wizard"
echo "    2. Generate an administrative access token"
echo "    3. Fire challenge syncing tools: ./scripts/challenges-load.sh"
echo "    4. Initialize user spaces: ./scripts/spawn-analysts.sh roster.txt"
echo "═══════════════════════════════════════════════════"