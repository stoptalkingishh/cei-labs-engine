#!/usr/bin/env bash
# scripts/platform-up.sh
# Deploys all CTF platform services to the K3s cluster in dependency order.
# Run this after ansible/site.yml has provisioned all nodes.
#
# Usage:
#     ./scripts/platform-up.sh
#     ./scripts/platform-up.sh --dry-run        show commands without running

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

DRY_RUN=false
VARS_FILE="ansible/group_vars/all.yml"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# Standardized logging helpers to enforce style consistency across scripts
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

verify_dependencies() {
  local missing_deps=()
  for cmd in kubectl helm ansible ansible-playbook jq curl; do
    if ! command -v "$cmd" &>/dev/null; then
      missing_deps+=("$cmd")
    fi
  done
  if [[ ${#missing_deps[@]} -gt 0 ]]; then
    log_error "Missing required system dependencies: ${missing_deps[*]}"
    log_warn "Please install the missing utilities and verify your PATH."
    exit 1
  fi
}

verify_cluster_reachable() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] Verifying Kubernetes cluster reachability via: kubectl cluster-info"
  else
    log_warn "Verifying Kubernetes cluster reachability..."
    if ! kubectl cluster-info &>/dev/null; then
      log_error "Kubernetes cluster is unreachable or unauthenticated."
      log_error "Ensure that K3s is running and your KUBECONFIG is correct."
      log_error "Current KUBECONFIG path: $KUBECONFIG"
      exit 1
    fi
    log_info "Cluster reachability confirmed."
  fi
}

# Run dependency verification right at startup
verify_dependencies

if [[ ! -f "$VARS_FILE" ]]; then
  log_error "ansible/group_vars/all.yml not found."
  log_error "Copy all.yml.example to all.yml and fill in all required values."
  log_error "Run: cp ansible/group_vars/all.yml.example ansible/group_vars/all.yml"
  exit 1
fi

# Standard While-Loop Argument Parser
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      log_error "Unknown validation parameter passed: $1"
      echo "Usage: $0 [--dry-run]"
      exit 1
      ;;
  esac
done

# Perform preflight cluster infrastructure connectivity check
verify_cluster_reachable

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] $*"
  else
    log_info "$*"
    # Execute array of arguments natively to avoid shell injection and parsing bugs inherent to eval
    "$@"
  fi
}

echo "═══════════════════════════════════════════════════"
echo "  CEI Labs Engine — Deployment Matrix"
echo "═══════════════════════════════════════════════════"

# ── 1. Namespaces ─────────────────────────────────────────────────────────────
echo ""
echo "── Step 1: Namespaces ──────────────────────────────"
run kubectl apply -f k8s/namespaces/namespaces.yml

# ── 2. Local Container Registry ───────────────────────────────────────────────
echo ""
echo "── Step 2: Internal Air-Gap Image Registry ─────────"
if [ -f "k8s/registry/registry.yml" ]; then
  run kubectl apply -f k8s/registry/registry.yml
  run kubectl rollout status deployment/local-registry -n registry --timeout=90s
else
  log_warn "Local registry configuration sheet missing. Skipping deployment..."
fi

# ── 3. Cert-Manager Core Setup ────────────────────────────────────────────────
echo ""
echo "── Step 3: Deploying Cert-Manager Core ──────────"
if [[ "$DRY_RUN" == "true" ]]; then
  run "helm repo add jetstack https://charts.jetstack.io"
else
  helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
fi
run helm repo update
run helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.14.4 \
  --set installCRDs=true \
  --wait

if [ -f "k8s/ingress/cert-issuer.yml" ]; then
  run kubectl apply -f k8s/ingress/cert-issuer.yml
fi

# ── 4. Traefik Ingress Controller ─────────────────────────────────────────────
echo ""
echo "── Step 4: Traefik v3 Ingress Core ─────────────────"
if [[ "$DRY_RUN" == "true" ]]; then
  run "helm repo add traefik https://traefik.github.io/charts"
else
  helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true
fi
run helm repo update

if [ -f "k8s/ingress/traefik-values.yml" ]; then
  run helm upgrade --install traefik traefik/traefik \
    --namespace traefik \
    --create-namespace \
    -f k8s/ingress/traefik-values.yml \
    --wait
else
  run helm upgrade --install traefik traefik/traefik \
    --namespace traefik \
    --create-namespace \
    --set service.type=LoadBalancer \
    --wait
fi

# ── 5. CTFd Stack ─────────────────────────────────────────────────────────────
echo ""
echo "── Step 5: CTFd Core Ecosystem (MariaDB + Redis) ───"

# FIXED (B2): Place structural configuration definitions in-cluster FIRST
run kubectl apply -f k8s/ctfd/ctfd-deployment.yml

# FIXED (B2): Patch secrets pipeline instantly executes while containers are running init checks
if [[ -x "./scripts/patch-secrets.sh" && "$DRY_RUN" == "false" ]]; then
  log_warn "Injecting secrets baseline values from group_vars..."
  ./scripts/patch-secrets.sh || log_warn "Secrets patch execution failed."
fi

run kubectl rollout status statefulset/ctfd-db -n ctfd --timeout=120s
run kubectl rollout status deployment/ctfd-redis -n ctfd --timeout=60s
run kubectl rollout status deployment/ctfd -n ctfd --timeout=120s
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

# MITIGATION (F2): Sanitize extracted parameters to eliminate shell metacharacters before passing to eval
MAX_INSTANCES="${MAX_INSTANCES//[^0-9]/}"
CTF_KEY="${CTF_KEY//[\'\`\"\;\&\$\|\<\>\(\)]/}"
ADMIN_PASS="${ADMIN_PASS//[\'\`\"\;\&\$\|\<\>\(\)]/}"

# FIXED: Wrapped parameter injections with safe escaped quoting blocks to ensure clean strings pass through eval paths
run helm upgrade --install multijuicer \
  oci://ghcr.io/juice-shop/multi-juicer/helm/multi-juicer \
  -f k8s/multijuicer/values.yml \
  --namespace multijuicer \
  --set juiceShop.maxInstances="${MAX_INSTANCES}" \
  --set ctfKey="${CTF_KEY}" \
  --set adminPassword="${ADMIN_PASS}" \
  --wait
echo "    MultiJuicer gate running at: http://juiceshop.ctf.local/balancer/admin"

# ── 7. Traefik Ingress Routes ─────────────────────────────────────────────────
echo ""
echo "── Step 7: Ingress Routing Topology ────────────────"
run kubectl apply -f k8s/ingress/traefik-ingress.yml

# ── 8. Automation Imports & Challenge Pipelines ──────────────────────────────
echo ""
echo "── Step 8: Post-Deployment Data Ingestion ──────────"
echo "    Run these manually sequentially after provisioning is complete:"
echo "      1. ./scripts/juice-shop-ctf-import.sh"
echo "      2. ./scripts/challenges-load.sh"

echo ""
echo "═══════════════════════════════════════════════════"
log_info "Platform deployment complete."
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