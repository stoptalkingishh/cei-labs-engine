#!/usr/bin/env bash
# D:\REPO\install.sh
#
# Fully offline installer for the CEI Labs CTF platform. Run this ON the
# air-gapped Fedora target, from the root of this drive (wherever it gets
# mounted there), e.g.:
#
#     cd /path/to/mounted/REPO
#     sudo ./install.sh
#
# It assumes NOTHING about internet access — every RPM, container image,
# and Python wheel it needs is already sitting under this same directory
# tree (rpms/, images/, wheels/), vendored ahead of time on a machine that
# DID have internet. See docs/BUNDLE-CONTENTS.md for exactly what's here
# and docs/KNOWN-GAPS.md for the one deliberate pinning gap.
#
# Steps (each is independent where possible — one failing does not abort
# the ones after it; see the final summary):
#   1. Sanity-check Fedora + basic host shape
#   2. Install vendored RPMs (Docker CE, ansible-core, git, python3, ...)
#   3. Enable/start Docker, init a single-node Swarm if needed
#   4. docker load every vendored image tar, verifying sha256 first
#   5. Install vendored Python wheels
#   6. Copy cei-labs-engine + CEI-Labs-Wargames into place
#   7. Deploy the stack via cei-labs-engine/scripts/stack-up.sh
#   8. Bootstrap CTFd's first-run admin account non-interactively
#   9. Run CEI-Labs-Wargames/deploy.sh to upload all challenges
#
set -uo pipefail  # deliberately NOT -e: steps must run even if an earlier one fails

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/cei-labs}"
LOG_FILE="$REPO_ROOT/install-run.log"
: > "$LOG_FILE"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[+]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "${RED}[-]${NC} $*" | tee -a "$LOG_FILE" >&2; }
log_step()  { echo -e "\n${BLUE}══ $* ══${NC}" | tee -a "$LOG_FILE"; }

# Track pass/fail per numbered step for the final summary. Never let a
# failure in one step stop later ones — that's the whole point of not
# using `set -e` here; every step function traps its own errors.
declare -A STEP_STATUS
declare -A STEP_NOTE
run_step() {
  local num="$1" name="$2" fn="$3"
  log_step "[$num/9] $name"
  if "$fn"; then
    STEP_STATUS[$num]="OK"
  else
    STEP_STATUS[$num]="FAILED"
    log_error "Step $num ($name) failed — see log above. Continuing with remaining steps."
  fi
}

# ─────────────────────────────────────────────────────────────────────────
# Step 1: sanity checks
# ─────────────────────────────────────────────────────────────────────────
step1_sanity() {
  if [[ ! -f /etc/os-release ]]; then
    log_error "/etc/os-release not found — cannot verify this is Fedora."
    return 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "fedora" ]]; then
    log_error "This host reports ID=${ID:-unknown} in /etc/os-release, not fedora. Refusing to continue — this bundle vendors Fedora RPMs only."
    return 1
  fi
  log_info "Detected Fedora ${VERSION_ID:-unknown} (${PRETTY_NAME:-unknown})."
  if [[ "${VERSION_ID:-}" != "44" ]]; then
    log_warn "This bundle's RPMs were resolved against Fedora 44 (fedora:44 container, matching the repo's validated station). You're on ${VERSION_ID:-unknown} — package availability/dependency closure may not match exactly."
  fi

  if [[ $EUID -ne 0 ]]; then
    log_error "This script must run as root (it installs RPMs, manages Docker/systemd, and writes to $INSTALL_ROOT). Re-run with sudo."
    return 1
  fi

  for d in rpms images wheels repos; do
    if [[ ! -d "$REPO_ROOT/$d" ]]; then
      log_error "$REPO_ROOT/$d not found — this doesn't look like a complete bundle."
      return 1
    fi
  done

  # No network reachability check performed on purpose: the whole point of
  # this script is to work with zero internet. We don't probe for it and
  # we don't fail if it's absent.
  log_info "Bundle layout looks complete at $REPO_ROOT."
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
# Step 2: install vendored RPMs
# ─────────────────────────────────────────────────────────────────────────
step2_rpms() {
  local rpm_dir="$REPO_ROOT/rpms"
  local count
  count=$(find "$rpm_dir" -maxdepth 1 -name '*.rpm' | wc -l)
  if [[ "$count" -eq 0 ]]; then
    log_error "No .rpm files found under $rpm_dir."
    return 1
  fi
  if [[ ! -f "$rpm_dir/repodata/repomd.xml" ]]; then
    log_error "$rpm_dir/repodata/repomd.xml missing — this bundle's RPM set needs its createrepo_c index to install correctly (see below), and it isn't there."
    return 1
  fi

  # Deliberately NOT `dnf install *.rpm` (flat @commandline install of every
  # file). Verified against a fresh fedora:44 container: that form makes
  # dnf treat mutually-exclusive alternative providers (wget1-wget vs.
  # wget2-wget, systemd-sysusers vs. systemd-standalone-sysusers, etc.) as
  # simultaneous hard requirements instead of letting its solver pick one,
  # and produces unresolvable conflicts. Installing by NAME against a repo
  # (this bundle's rpms/repodata/, built by `createrepo_c` when the RPMs
  # were vendored) lets dnf's normal depsolver choose correctly, matching
  # exactly how `dnf install <name>` behaves against any other repo.
  # --disablerepo='*' turns off every configured/system repo (including
  # anything in /etc/yum.repos.d/), so this only ever touches local files.
  log_info "Installing vendored packages from $rpm_dir ($count RPM files) via dnf's solver against the local repo index (no other repo enabled)..."
  if ! dnf install -y --disablerepo='*' --repofrompath=local,"file://$rpm_dir" --setopt=local.gpgcheck=0 \
      btop curl fail2ban git htop jq tmux unzip wget2 \
      firewalld python3-firewall \
      ansible-core python3 python3-pip \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
      >>"$LOG_FILE" 2>&1; then
    log_error "dnf install of vendored RPMs failed — see $LOG_FILE for the full transcript."
    return 1
  fi
  log_info "RPM install complete."
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
# Step 3: Docker + Swarm
# ─────────────────────────────────────────────────────────────────────────
step3_docker_swarm() {
  if ! command -v docker &>/dev/null; then
    log_error "docker CLI not found after RPM install — step 2 likely failed to install docker-ce."
    return 1
  fi

  systemctl enable --now docker >>"$LOG_FILE" 2>&1
  if ! systemctl is-active --quiet docker; then
    log_error "Docker service did not start. Check: systemctl status docker"
    return 1
  fi
  log_info "Docker service is active."

  local swarm_state
  swarm_state=$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo "inactive")
  if [[ "$swarm_state" != "active" ]]; then
    # `docker swarm init` with no --advertise-addr fails outright on any
    # host with more than one address on its primary interface — e.g. a
    # real Wi-Fi NIC with both an IPv4 and multiple global/temporary IPv6
    # addresses, which is normal on real hardware and never showed up in
    # the throwaway single-NIC containers this was built against
    # ("could not choose an IP address to advertise since this system has
    # multiple addresses on interface ..."). Resolve the address the
    # kernel would actually route out on ourselves instead of leaving it
    # to Docker's own (single-address-only) heuristic.
    local advertise_addr
    advertise_addr=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
    if [[ -z "$advertise_addr" ]]; then
      advertise_addr=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    if [[ -z "$advertise_addr" ]]; then
      log_error "Could not determine an IPv4 address to advertise for Docker Swarm (checked 'ip route get' and 'hostname -I')."
      return 1
    fi
    log_info "Initializing a single-node Docker Swarm (advertising $advertise_addr)..."
    if ! docker swarm init --advertise-addr "$advertise_addr" >>"$LOG_FILE" 2>&1; then
      log_error "docker swarm init failed."
      return 1
    fi
  else
    log_info "Swarm already active on this host."
  fi

  local self_id
  self_id=$(docker node inspect self --format '{{.ID}}' 2>/dev/null || echo "")
  if [[ -n "$self_id" ]]; then
    docker node update --label-add ctfd-data=true "$self_id" >>"$LOG_FILE" 2>&1 || true
  fi
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
# Step 4: docker load every image tar, sha256-verified against MANIFEST.json
# ─────────────────────────────────────────────────────────────────────────
step4_load_images() {
  local manifest="$REPO_ROOT/images/MANIFEST.json"
  if [[ ! -f "$manifest" ]]; then
    log_error "$manifest not found — cannot verify image tars before loading."
    return 1
  fi
  if ! command -v docker &>/dev/null; then
    log_error "docker CLI unavailable — step 3 must succeed before this step can run."
    return 1
  fi

  # Extract "file" + "sha256" pairs without assuming jq is installed yet
  # (jq itself is one of the vendored RPMs, but don't make step 4 depend on
  # step 2/3 succeeding in a specific order beyond docker being present).
  local py
  py=$(command -v python3 || command -v python)
  if [[ -z "$py" ]]; then
    log_error "No python3/python found to parse MANIFEST.json."
    return 1
  fi

  local ok=0 fail=0
  while IFS=$'\t' read -r file sha; do
    [[ -z "$file" ]] && continue
    local path="$REPO_ROOT/images/$file"
    if [[ ! -f "$path" ]]; then
      log_error "Missing image tar: $path"
      fail=$((fail+1))
      continue
    fi
    local actual
    actual=$(sha256sum "$path" | awk '{print $1}')
    if [[ "$actual" != "$sha" ]]; then
      log_error "SHA256 mismatch for $file: expected $sha, got $actual — refusing to load."
      fail=$((fail+1))
      continue
    fi
    log_info "Loading $file (sha256 verified)..."
    if docker load -i "$path" >>"$LOG_FILE" 2>&1; then
      ok=$((ok+1))
    else
      log_error "docker load failed for $file"
      fail=$((fail+1))
    fi
  done < <("$py" - "$manifest" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
for section in ("base_images", "custom_images"):
    for entry in d.get(section, []):
        print(f"{entry['file']}\t{entry['sha256']}")
PYEOF
)

  log_info "Image load: $ok succeeded, $fail failed."

  # Re-tag the custom images under every name the rest of the platform
  # actually looks for by default (see docs/BUNDLE-CONTENTS.md for why
  # there are two names per image — this bundle builds everything under
  # a single ':offline' tag, but spawn-workspaces.sh/stack.yml expect
  # ':${IMAGE_TAG}' while the Wargames build_*.py scripts default their
  # target_image references to ':latest'). Both tags point at the same
  # image ID, so this costs no extra disk.
  local ENGINE_NS=ghcr.io/stoptalkingishh/cei-labs-engine
  local WARGAMES_NS=ghcr.io/stoptalkingishh/cei-labs-wargames
  declare -A RETAGS=(
    ["$ENGINE_NS/analyst:offline"]="$ENGINE_NS/ctf-analyst:offline $ENGINE_NS/ctf-analyst:latest"
    ["$ENGINE_NS/kali-novnc:offline"]="$ENGINE_NS/ctf-kali-novnc:offline $ENGINE_NS/ctf-kali-novnc:latest"
    ["$WARGAMES_NS/bandit:offline"]="$WARGAMES_NS/bandit-target:latest $WARGAMES_NS/bandit-target:offline"
    ["$WARGAMES_NS/krypton:offline"]="$WARGAMES_NS/krypton-target:latest $WARGAMES_NS/krypton-target:offline"
    ["$WARGAMES_NS/natas:offline"]="$WARGAMES_NS/natas-target:latest $WARGAMES_NS/natas-target:offline"
  )
  for src in "${!RETAGS[@]}"; do
    for dst in ${RETAGS[$src]}; do
      docker tag "$src" "$dst" >>"$LOG_FILE" 2>&1 \
        && log_info "Tagged $src -> $dst" \
        || log_warn "Could not tag $src -> $dst (source image may not have loaded — see above)"
    done
  done

  [[ "$fail" -eq 0 ]]
}

# ─────────────────────────────────────────────────────────────────────────
# Step 5: install vendored Python wheels
# ─────────────────────────────────────────────────────────────────────────
step5_wheels() {
  local wheel_dir="$REPO_ROOT/wheels"
  local count
  count=$(find "$wheel_dir" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) | wc -l)
  if [[ "$count" -eq 0 ]]; then
    log_error "No wheels found under $wheel_dir."
    return 1
  fi

  # The orchestrator and CTFd plugin's Python deps are already baked into
  # their respective docker images (both Dockerfiles run pip install
  # in-container at build time, which happened on the build machine where
  # this bundle was produced — not something the target needs to redo).
  # These wheels exist for two things the target host itself does:
  #   - ctfcli + pyyaml, needed to run CEI-Labs-Wargames/deploy.sh (step 9)
  #     directly on the host (outside any container).
  # Installed into a dedicated venv so this never fights the RPM-installed
  # system python3/pip from step 2. --system-site-packages so it inherits
  # the RPM-installed pyyaml: the vendored pyyaml wheel is built for the
  # build machine's Python ABI (cp312), which doesn't match every target's
  # system python3 (e.g. Fedora 44 ships Python 3.14) — pip has no matching
  # wheel to fall back to since this is a --no-index offline install.
  local venv="$INSTALL_ROOT/venv"
  mkdir -p "$INSTALL_ROOT"
  if ! python3 -m venv --system-site-packages "$venv" >>"$LOG_FILE" 2>&1; then
    log_error "python3 -m venv failed — is python3-pip/python3 fully installed?"
    return 1
  fi
  if ! "$venv/bin/python3" -c "import yaml" >>"$LOG_FILE" 2>&1; then
    log_error "pyyaml isn't available via the system site-packages inherited into $venv — is the RPM-installed python3-pyyaml present from step 2?"
    return 1
  fi
  if ! "$venv/bin/pip" install --no-index --find-links "$wheel_dir" ctfcli >>"$LOG_FILE" 2>&1; then
    log_error "Offline pip install of ctfcli into $venv failed — see $LOG_FILE."
    return 1
  fi
  log_info "Installed ctfcli offline into $venv from $count vendored wheel files; pyyaml inherited from the RPM-installed system package via --system-site-packages."
  log_info "(flask/docker/gunicorn/requests wheels are also vendored here for reference/rebuild use, but are not installed on the host — they're already baked into the ctfd/orchestrator images.)"
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
# Step 6: copy repos into place
# ─────────────────────────────────────────────────────────────────────────
step6_copy_repos() {
  mkdir -p "$INSTALL_ROOT"
  local ok=true
  for repo in cei-labs-engine CEI-Labs-Wargames cei-labs-net cei-labs-event; do
    local src="$REPO_ROOT/repos/$repo"
    local dst="$INSTALL_ROOT/$repo"
    if [[ ! -d "$src" ]]; then
      log_error "Missing $src"
      ok=false
      continue
    fi
    log_info "Copying $repo -> $dst"
    rm -rf "$dst"
    if ! cp -r "$src" "$dst"; then
      log_error "Copy failed for $repo"
      ok=false
    fi
  done
  # Copying (not just referencing D:\REPO in place) so the deployment
  # survives the USB drive being unplugged later, and so docker/.env,
  # docker/secrets/, and ctfcli's .ctf/config below don't get written back
  # onto the install media. See docs/BUNDLE-CONTENTS.md, "why copy not
  # reference".
  $ok
}

# ─────────────────────────────────────────────────────────────────────────
# Step 7: deploy the stack
# ─────────────────────────────────────────────────────────────────────────
step7_deploy_stack() {
  local engine_dir="$INSTALL_ROOT/cei-labs-engine"
  if [[ ! -d "$engine_dir" ]]; then
    log_error "$engine_dir missing — step 6 must succeed first."
    return 1
  fi
  cd "$engine_dir" || return 1

  if [[ ! -f docker/.env ]]; then
    cp docker/.env.example docker/.env
  fi
  # This bundle builds/tags every custom image as GITHUB_ORG=stoptalkingishh,
  # IMAGE_TAG=offline (see step 4's retagging) — point stack.yml's variable
  # interpolation at exactly that so `docker stack deploy` resolves images
  # from what was just docker-loaded, not from a registry it can't reach.
  sed -i 's/^GITHUB_ORG=.*/GITHUB_ORG=stoptalkingishh/' docker/.env
  sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=offline/' docker/.env
  if ! grep -q '^IMAGE_TAG=' docker/.env; then echo 'IMAGE_TAG=offline' >> docker/.env; fi
  if ! grep -q '^GITHUB_ORG=' docker/.env; then echo 'GITHUB_ORG=stoptalkingishh' >> docker/.env; fi

  if [[ ! -d docker/secrets ]]; then
    cp -r docker/secrets.example docker/secrets
    log_warn "docker/secrets/ was populated from secrets.example with CHANGE_ME placeholders."
    log_warn "Generating random values for every CHANGE_ME secret now (non-interactive install) — rotate them before a real event if that matters to you."
    for f in docker/secrets/*.txt; do
      # credential_encryption_key needs a real Fernet key, not an arbitrary
      # alnum string (app/crypto.py hands it straight to
      # cryptography.fernet.Fernet(), which requires urlsafe-base64-encoded
      # 32 raw bytes) -- handled separately below instead of here.
      if [[ "$(basename "$f")" == "credential_encryption_key.txt" ]]; then
        continue
      fi
      if grep -q CHANGE_ME "$f"; then
        head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$f"
      fi
    done
    if grep -q CHANGE_ME docker/secrets/credential_encryption_key.txt 2>/dev/null; then
      python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"         > docker/secrets/credential_encryption_key.txt 2>/dev/null         || python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"           > docker/secrets/credential_encryption_key.txt
    fi
  fi

  # This is a fully offline install with no DNS server standing up
  # BASE_DOMAIN's records — nothing else in this script (or in
  # stack-up.sh) makes `ctfd.$BASE_DOMAIN` resolve anywhere. traefik's
  # own router rule for CTFd also matches a bare dotted-IP Host header
  # (see stack.yml's HostRegexp clause), which is how the stack is
  # reachable at all right now, but step 8's bootstrap and any browser
  # pointed at the documented https://ctfd.$BASE_DOMAIN URL both need the
  # name to actually resolve on this host. Point it at loopback via
  # /etc/hosts, idempotently.
  local base_domain
  base_domain=$(grep '^BASE_DOMAIN=' docker/.env 2>/dev/null | cut -d= -f2)
  base_domain="${base_domain:-ctf.local}"
  if ! getent hosts "ctfd.${base_domain}" >>"$LOG_FILE" 2>&1; then
    log_info "ctfd.${base_domain} does not resolve — adding a loopback entry to /etc/hosts."
    echo "127.0.0.1 ctfd.${base_domain}" >> /etc/hosts
  fi

  log_info "Running scripts/stack-up.sh..."
  if ! ./scripts/stack-up.sh >>"$LOG_FILE" 2>&1; then
    log_error "scripts/stack-up.sh failed — see $LOG_FILE. (Common cause offline: docker/.env's BASE_DOMAIN doesn't resolve/have a cert — this install doesn't set up DNS or TLS for you; see docs/network-prerequisites.md in the engine repo, or use the default ctf.local + a self-signed cert.)"
    return 1
  fi
  log_info "Stack deployed."
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
# Step 8: bootstrap CTFd's first-run admin
# ─────────────────────────────────────────────────────────────────────────
CTFD_ADMIN_TOKEN_FILE="/root/.cei-labs-ctfd-admin-token"
step8_bootstrap_ctfd() {
  local engine_dir="$INSTALL_ROOT/cei-labs-engine"
  local base_domain
  base_domain=$(grep '^BASE_DOMAIN=' "$engine_dir/docker/.env" 2>/dev/null | cut -d= -f2)
  base_domain="${base_domain:-ctf.local}"
  local url="https://ctfd.${base_domain}"

  # setup_local_ctfd.py (CEI-Labs-Wargames/scripts/local-ctfd/) does this
  # exact nonce/setup/login/token dance already, but against the *local
  # test* compose stack on plain http://localhost:8000 with no TLS. Adapted
  # inline here for the real stack.yml deployment instead of forced through
  # that script unmodified: we need `-k` (self-signed cert, matching
  # scripts/install.sh's own setup_ctfd()) and the real https://ctfd.<domain>
  # host, which setup_local_ctfd.py has no concept of.
  local venv="$INSTALL_ROOT/venv"
  local py="$venv/bin/python3"
  [[ -x "$py" ]] || py="python3"

  log_info "Waiting for CTFd to answer at $url/setup (up to 5 minutes)..."
  local up=false
  for _ in $(seq 1 60); do
    if curl -sfk -o /dev/null "$url/setup"; then up=true; break; fi
    sleep 5
  done
  if [[ "$up" != "true" ]]; then
    log_error "CTFd never came up at $url/setup within 5 minutes. Check: docker service logs cei-labs_ctfd"
    return 1
  fi

  local admin_pass
  admin_pass=$(head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32)

  local out
  out=$("$py" - "$url" "$admin_pass" <<'PYEOF'
import re, sys
import urllib.request, urllib.error, http.cookiejar

base_url, admin_pass = sys.argv[1], sys.argv[2]

import ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# The TLS context has to be bound at build_opener() time via HTTPSHandler —
# urllib's OpenerDirector.open() has never accepted a context= kwarg in any
# Python version; passing it there raises "unexpected keyword argument".
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cj),
    urllib.request.HTTPSHandler(context=ctx),
)

def get(path):
    req = urllib.request.Request(base_url + path)
    return opener.open(req, timeout=30).read().decode()

def post(path, data: dict):
    import urllib.parse
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(base_url + path, data=body, method="POST")
    return opener.open(req, timeout=30)

def nonce(html):
    m = re.search(r'name="nonce"[^>]*value="([a-f0-9]+)"', html) or \
        re.search(r'value="([a-f0-9]+)"[^>]*name="nonce"', html)
    if not m:
        raise SystemExit("could not find setup nonce")
    return m.group(1)

try:
    setup_html = get("/setup")
except urllib.error.HTTPError as e:
    print(f"ALREADY_SETUP_OR_ERROR:{e.code}")
    sys.exit(0)

n = nonce(setup_html)
post("/setup", {
    "nonce": n,
    "ctf_name": "CEI Labs Cyber Range",
    "ctf_description": "Offline install",
    "name": "admin",
    "email": "admin@ctf.local",
    "password": admin_pass,
    "user_mode": "teams",
    "setup": "true",
})

login_html = get("/login")
n = nonce(login_html)
post("/login", {"name": "admin", "password": admin_pass, "nonce": n})

settings_html = get("/settings")
n2 = re.search(r"csrfNonce['\"]?\s*:\s*[\"']([a-f0-9]+)[\"']", settings_html)
if not n2:
    raise SystemExit("could not find settings CSRF nonce")

import json
req = urllib.request.Request(
    base_url + "/api/v1/tokens",
    data=json.dumps({"expiration": "2030-01-01", "description": "offline-install bootstrap"}).encode(),
    headers={"Content-Type": "application/json", "CSRF-Token": n2.group(1)},
    method="POST",
)
resp = opener.open(req, timeout=30)
token = json.loads(resp.read())["data"]["value"]
print(f"TOKEN:{token}")
PYEOF
)
  if [[ "$out" == TOKEN:* ]]; then
    local token="${out#TOKEN:}"
    echo "$token" > "$CTFD_ADMIN_TOKEN_FILE"
    chmod 600 "$CTFD_ADMIN_TOKEN_FILE"
    echo "admin / $admin_pass" > "${CTFD_ADMIN_TOKEN_FILE}.admin-password"
    chmod 600 "${CTFD_ADMIN_TOKEN_FILE}.admin-password"
    log_info "CTFd bootstrapped. Admin token written to $CTFD_ADMIN_TOKEN_FILE, admin password to ${CTFD_ADMIN_TOKEN_FILE}.admin-password."
    return 0
  elif [[ "$out" == ALREADY_SETUP_OR_ERROR:* ]]; then
    log_warn "CTFd's /setup did not return 200 (already initialized, or another error: ${out#ALREADY_SETUP_OR_ERROR:}). If it's already set up, log in manually and generate an API token, then: echo TOKEN > $CTFD_ADMIN_TOKEN_FILE"
    return 1
  else
    log_error "CTFd bootstrap script produced unexpected output: $out"
    return 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────
# Step 9: upload all challenges via CEI-Labs-Wargames/deploy.sh
# ─────────────────────────────────────────────────────────────────────────
step9_deploy_challenges() {
  local wargames_dir="$INSTALL_ROOT/CEI-Labs-Wargames"
  local engine_dir="$INSTALL_ROOT/cei-labs-engine"
  if [[ ! -d "$wargames_dir" ]]; then
    log_error "$wargames_dir missing — step 6 must succeed first."
    return 1
  fi
  if [[ ! -f "$CTFD_ADMIN_TOKEN_FILE" ]]; then
    log_error "No CTFd admin token at $CTFD_ADMIN_TOKEN_FILE (step 8 must have failed). Attempting step 9 anyway will fail without CTFD_URL/CTFD_TOKEN — skipping the actual upload, but this is exactly the kind of independent-step failure this installer is designed to surface rather than hide: fix step 8 manually, then re-run:"
    log_error "    export CTFD_URL=... CTFD_TOKEN=... CTFD_SYNC_SECRET=\$(cat $engine_dir/docker/secrets/plugin_shared_secret.txt)"
    log_error "    cd $wargames_dir && ./deploy.sh"
    return 1
  fi

  local base_domain
  base_domain=$(grep '^BASE_DOMAIN=' "$engine_dir/docker/.env" 2>/dev/null | cut -d= -f2)
  base_domain="${base_domain:-ctf.local}"

  local venv="$INSTALL_ROOT/venv"
  export PATH="$venv/bin:$PATH"

  cd "$wargames_dir" || return 1
  export CTFD_URL="https://ctfd.${base_domain}"
  export CTFD_TOKEN
  CTFD_TOKEN=$(cat "$CTFD_ADMIN_TOKEN_FILE")
  export CTFD_INSECURE=true
  export CTFD_SYNC_SECRET
  CTFD_SYNC_SECRET=$(cat "$engine_dir/docker/secrets/plugin_shared_secret.txt" 2>/dev/null || echo "")
  if [[ -z "$CTFD_SYNC_SECRET" ]]; then
    log_error "Could not read $engine_dir/docker/secrets/plugin_shared_secret.txt — deploy.sh requires CTFD_SYNC_SECRET."
    return 1
  fi

  # The wargames target images are pinned to ':latest' by default in
  # scripts/build_bandit.py / build_krypton.py / build_natas.py; step 4
  # already retagged the loaded images to match those exact names, so no
  # BANDIT_IMAGE/KRYPTON_IMAGE/NATAS_*_IMAGE overrides are needed here.
  if ! ./deploy.sh >>"$LOG_FILE" 2>&1; then
    log_error "deploy.sh failed — see $LOG_FILE."
    return 1
  fi
  log_info "Challenges uploaded."
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
main() {
  run_step 1 "Sanity checks"              step1_sanity
  run_step 2 "Install vendored RPMs"      step2_rpms
  run_step 3 "Docker + Swarm"             step3_docker_swarm
  run_step 4 "Load vendored images"       step4_load_images
  run_step 5 "Install vendored wheels"    step5_wheels
  run_step 6 "Copy repos into place"      step6_copy_repos
  run_step 7 "Deploy the stack"           step7_deploy_stack
  run_step 8 "Bootstrap CTFd admin"       step8_bootstrap_ctfd
  run_step 9 "Upload challenges"          step9_deploy_challenges

  echo -e "\n${BLUE}══════════════════════ SUMMARY ══════════════════════${NC}"
  for n in 1 2 3 4 5 6 7 8 9; do
    local status="${STEP_STATUS[$n]:-SKIPPED}"
    if [[ "$status" == "OK" ]]; then
      echo -e "  [${GREEN}OK${NC}]     Step $n"
    else
      echo -e "  [${RED}FAILED${NC}] Step $n"
    fi
  done
  echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
  echo "Full log: $LOG_FILE"
  if [[ -f "$CTFD_ADMIN_TOKEN_FILE" ]]; then
    echo "CTFd admin token: $CTFD_ADMIN_TOKEN_FILE"
    echo "CTFd admin password: ${CTFD_ADMIN_TOKEN_FILE}.admin-password"
  fi

  local any_failed=false
  for n in 1 2 3 4 5 6 7 8 9; do
    [[ "${STEP_STATUS[$n]:-}" == "FAILED" ]] && any_failed=true
  done
  $any_failed && exit 1
  exit 0
}

main "$@"
