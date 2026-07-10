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

# Security: VNC_PASSWORD is intentionally NOT an ARG/ENV here. Baking a
# password into an image means every container ever run from it shares one
# never-rotated credential, recoverable via `docker inspect`/`docker
# history` by anyone who can pull the image (no shell access needed) --
# `ghcr.io/stoptalkingishh/*` is public. The account is created with no
# usable password below; the real password is applied fresh at container
# START from the RUNTIME environment by /start.sh (see the end of this
# file), which is what the orchestrator's plan_range_attacker() and
# scripts/spawn-workspaces.sh both already generate per-instance --
# neither ever wrote to a running container before this, since nothing
# re-read the env var after build time.

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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Multiple ways to interact with this box, deliberately (this is a
# web-exploitation-focused attacker workstation as well as a general
# analyst box): noVNC for a full desktop in the browser, SSH for players
# who are more comfortable at a terminal. Same account, same password,
# either way.
RUN mkdir -p /var/run/sshd \
    && ssh-keygen -A \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

RUN pip install --no-cache-dir --break-system-packages volatility3

# Verify vncpasswd exists after installation
RUN command -v vncpasswd

# Create operator user with NO usable password (locked account) -- the
# real password is applied at container start, below, from the runtime
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

# Applies the Linux account password and the VNC-specific password file
# fresh on every container start, from whatever VNC_PASSWORD is actually
# in this specific container's runtime environment -- never baked into
# the image, never shared across teams/instances. Fails loudly and
# refuses to start rather than silently falling back to no password if
# the orchestrator/spawn script didn't set one.
RUN cat > /start.sh <<'SCRIPT'
#!/bin/bash
set -e

if [ -z "${VNC_PASSWORD:-}" ]; then
    echo "FATAL: VNC_PASSWORD is not set in the container environment -- refusing to start with no password." >&2
    exit 1
fi

echo "operator:${VNC_PASSWORD}" | chpasswd
su operator -c "mkdir -p ~/.config/tigervnc && echo \"${VNC_PASSWORD}\" | vncpasswd -f > ~/.config/tigervnc/passwd && chmod 600 ~/.config/tigervnc/passwd"

su operator -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost no"
su operator -c "websockify --web=/usr/share/novnc 6080 localhost:5901" &
/usr/sbin/sshd
tail -f /dev/null
SCRIPT
RUN chmod +x /start.sh \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080 22

HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]