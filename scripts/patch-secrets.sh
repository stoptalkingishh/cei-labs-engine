#!/usr/bin/env bash
# scripts/patch-secrets.sh
# Rotates one or more Docker secrets and rolls the services that use them.
#
# Docker stack secrets defined with `file:` (see docker/stack.yml) are
# content-addressed: when you edit a file under docker/secrets/ and redeploy,
# Docker computes a new secret automatically, updates every service that
# references it with a rolling restart, and removes the old secret version —
# there is no manual "patch a live secret" step like the old k8s Secret +
# `kubectl rollout restart` dance this replaces.
#
# Usage:
#   1. Edit the relevant file(s) under docker/secrets/
#   2. ./scripts/patch-secrets.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[-]${NC} $*" >&2; }

if [[ ! -d "$DOCKER_DIR/secrets" ]]; then
  log_error "docker/secrets/ not found. Run: cp -r docker/secrets.example docker/secrets"
  exit 1
fi

placeholder_found=false
for example in "$DOCKER_DIR"/secrets.example/*.txt; do
  name="$(basename "$example")"
  real="$DOCKER_DIR/secrets/$name"
  if [[ -f "$real" ]] && grep -q "CHANGE_ME" "$real"; then
    log_warn "docker/secrets/$name still contains a CHANGE_ME placeholder."
    placeholder_found=true
  fi
done
if [[ "$placeholder_found" == "true" ]]; then
  read -rp "Some secrets look unfilled — continue rotating anyway? [y/N] " CONTINUE
  [[ "${CONTINUE,,}" == "y" ]] || exit 1
fi

log_info "Redeploying stack so Docker picks up any changed secret content..."
cd "$DOCKER_DIR"
docker stack deploy --with-registry-auth -c stack.yml cei-labs

log_info "Done. Services referencing a changed secret roll automatically — check with: docker stack ps cei-labs"
