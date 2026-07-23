# Pinned to a real digest, not the floating :latest tag -- combined with
# this image's CI build using `no-cache: true`, an unpinned :latest base
# meant every rebuild could silently pull a different (potentially
# compromised) upstream layer with no record of what changed. Pinning
# the base layer doesn't freeze installed *package* versions below (the
# apt-get install step still pulls current versions from Kali's rolling
# repos, which is intentional here -- pentesting tools like nmap/sqlmap/
# hashcat benefit from staying current), but it does mean the base OS
# layer itself is reproducible and auditable rather than a moving
# target. Bump this digest deliberately (not automatically) when a base
# refresh is actually wanted -- `docker pull kalilinux/kali-rolling:latest`
# and copy the reported digest.
FROM kalilinux/kali-rolling@sha256:776d57c9d607faafef9957073b0b5a05b0d1115e5728777a8fc5588827cfd249

ARG DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Security: neither VNC_PASSWORD nor OPERATOR_PASSWORD is an ARG/ENV
# here. Baking a password into an image means every container ever run
# from it shares one never-rotated credential, recoverable via `docker
# inspect`/`docker history` by anyone who can pull the image (no shell
# access needed) -- `ghcr.io/stoptalkingishh/*` is public. The account is
# created with no usable password below; the real passwords are applied
# fresh at container START from the RUNTIME environment by /start.sh (see
# the end of this file), which is what the orchestrator's
# plan_range_attacker() and scripts/spawn-workspaces.sh both already
# generate per-instance -- neither ever wrote to a running container
# before this, since nothing re-read the env vars after build time.
#
# These are two SEPARATE credentials for the same "operator" account, not
# one shared string: OPERATOR_PASSWORD drives the Linux account (chpasswd,
# used by SSH) and can be arbitrarily long/high-entropy; VNC_PASSWORD
# drives TigerVNC's own auth (vncpasswd) and is generated at exactly 8
# characters by the orchestrator, since TigerVNC's protocol silently
# truncates anything longer -- `vncpasswd -f` doesn't warn, it just
# discards bytes past the 8th. Using one shared long value for both (as
# this image previously did) meant VNC would silently only ever honor
# the first 8 characters of whatever was displayed as "the" password.

RUN apt-get update && apt-get install -y --no-install-recommends \
    xfce4 \
    xfce4-terminal \
    tigervnc-standalone-server \
    tigervnc-tools \
    novnc \
    websockify \
    wireshark \
    nmap \
    gobuster \
    sqlmap \
    john \
    hashcat \
    sleuthkit \
    binwalk \
    foremost \
    netcat-traditional \
    ncat \
    socat \
    tshark \
    tcpdump \
    python3 \
    python3-pip \
    python3-requests \
    default-jdk \
    curl \
    wget \
    git \
    tmux \
    vim \
    nano \
    dbus-x11 \
    unzip \
    ghidra \
    openssh-server \
    ffuf \
    mitmproxy \
    chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# The desktop's "Web Browser" launcher icon runs `exo-open --launch
# WebBrowser %u`, which (per this image's /etc/xdg/xfce4/helpers.rc,
# default WebBrowser=debian-sensible-browser) execs `sensible-browser`.
# That script tries `$BROWSER`, then -- since an X `$DISPLAY` is always
# set in this VNC desktop session -- `x-www-browser`, falling back to
# plain `www-browser` only when `$DISPLAY` is unset (e.g. a bare SSH
# session with no desktop). Debian's chromium package's postinst
# already registers /usr/bin/chromium as the `update-alternatives`
# provider for x-www-browser (and gnome-www-browser) automatically, so
# the noVNC desktop launcher works with no extra step -- verified live:
# `update-alternatives --list x-www-browser` reports /usr/bin/chromium
# right after `apt-get install chromium`, with no manual registration.
# It does NOT register a `www-browser` binary/alternative though (only
# `Provides: www-browser` as a dpkg virtual package, which is not the
# same as a binary on PATH) -- confirmed live: `command -v www-browser`
# fails after installing chromium alone. That path only matters for the
# no-$DISPLAY sensible-browser fallback and for anything invoking
# `www-browser` directly by name (this project's own compatibility
# convention), so provide it explicitly here rather than relying on
# apt packaging behavior that could change.
RUN command -v chromium \
    && update-alternatives --list x-www-browser \
    && ln -sf /usr/bin/chromium /usr/local/bin/www-browser \
    && command -v www-browser

# Natas per-level tab theming (favicon + title only -- native
# window-chrome theming would need a custom Chromium build or a signed
# Chrome Web Store theme, neither practical here; see
# natas-theme-extension/README.md for the full investigation). Loaded
# as an UNPACKED extension via --load-extension, which needs no
# signing/store submission. Implemented as a wrapper script at the same
# /usr/bin/chromium path the apt package installed to, rather than
# touching update-alternatives -- every existing launcher
# (exo-open -> sensible-browser -> x-www-browser, and the
# /usr/local/bin/www-browser symlink above) already resolves to this
# path, so the flag is picked up with no other launcher change needed.
COPY natas-theme-extension/ /opt/cei-natas-theme-extension/
RUN mv /usr/bin/chromium /usr/bin/chromium.real \
    && printf '%s\n' \
       '#!/bin/bash' \
       'exec /usr/bin/chromium.real --load-extension=/opt/cei-natas-theme-extension "$@"' \
       > /usr/bin/chromium \
    && chmod +x /usr/bin/chromium \
    && chown -R root:root /opt/cei-natas-theme-extension \
    && chmod -R a+rX /opt/cei-natas-theme-extension

# Multiple ways to interact with this box, deliberately (this is a
# web-exploitation-focused attacker workstation as well as a general
# analyst box): noVNC for a full desktop in the browser, SSH for players
# who are more comfortable at a terminal. Same account either way, but
# with two DIFFERENT passwords -- see the OPERATOR_PASSWORD/VNC_PASSWORD
# comment above and in /start.sh below.
RUN mkdir -p /var/run/sshd \
    && ssh-keygen -A \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

RUN pip install --no-cache-dir --break-system-packages volatility3

# Verify vncpasswd exists after installation
RUN command -v vncpasswd

# Create operator user with NO usable password (locked account) -- the
# real passwords are applied at container start, below, from the runtime
# environment.
RUN useradd -m -s /bin/bash --no-log-init --no-user-group operator

USER operator

# Kali's current TigerVNC (1.13+) uses the XDG-compliant
# ~/.config/tigervnc/ path, not the legacy ~/.vnc/ — writing directly here
# avoids TigerVNC's own ~/.vnc -> ~/.config/tigervnc auto-migration, which
# fails outright in this environment ("Could not migrate ... to
# .../.config/tigervnc") rather than falling back to the legacy path.
# xfce4-session must run in the FOREGROUND here -- backgrounding it (the
# old `xfce4-session &` pattern) makes this script return immediately,
# which current TigerVNC treats as "session exited too early" and tears
# the whole server down rather than tolerating a backgrounded WM.
RUN mkdir -p /home/operator/.config/tigervnc \
    && printf '#!/bin/bash\nexec xfce4-session\n' > /home/operator/.config/tigervnc/xstartup \
    && chmod +x /home/operator/.config/tigervnc/xstartup

USER root

# Applies the Linux account password (OPERATOR_PASSWORD, used by SSH via
# chpasswd) and the VNC-specific password file (VNC_PASSWORD, used by
# vncpasswd -- TigerVNC silently truncates this to 8 characters, which is
# exactly what the orchestrator generates it as, so there's no truncation
# surprise) fresh on every container start, from whatever's actually in
# this specific container's runtime environment -- never baked into the
# image, never shared across teams/instances. These are deliberately TWO
# SEPARATE variables driving two separate credentials for the same
# account, not one value reused for both -- see the Dockerfile-level
# comment above. Fails loudly and refuses to start rather than silently
# falling back to no password if the orchestrator/spawn script didn't set
# either one. The single quotes intentionally preserve runtime
# environment expansion.
# hadolint ignore=SC2016
RUN printf '%s\n' \
      '#!/bin/bash' \
      'set -e' \
      '' \
      'if [ -z "${OPERATOR_PASSWORD:-}" ]; then' \
      '    echo "FATAL: OPERATOR_PASSWORD is not set in the container environment -- refusing to start with no password." >&2' \
      '    exit 1' \
      'fi' \
      'if [ -z "${VNC_PASSWORD:-}" ]; then' \
      '    echo "FATAL: VNC_PASSWORD is not set in the container environment -- refusing to start with no password." >&2' \
      '    exit 1' \
      'fi' \
      '' \
      'echo "operator:${OPERATOR_PASSWORD}" | chpasswd' \
      'su operator -c "mkdir -p ~/.config/tigervnc && echo \"${VNC_PASSWORD}\" | vncpasswd -f > ~/.config/tigervnc/passwd && chmod 600 ~/.config/tigervnc/passwd"' \
      '' \
      'su operator -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost no"' \
      'su operator -c "websockify --web=/usr/share/novnc 6080 localhost:5901" &' \
      '/usr/sbin/sshd' \
      'tail -f /dev/null' \
      > /start.sh \
    && chmod +x /start.sh \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080 22

HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]
