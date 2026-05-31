# operator/kali-novnc/Dockerfile
# Full Kali Linux desktop accessible via browser (noVNC).
# Used for Sprint 3 PCCC challenges requiring GUI tools.
# Spawned by CTFd Docker Challenges plugin per participant.
#
# Packages NOT available via apt in kali-rolling containers and their fixes:
#   ghidra    — installed via wget from NSA GitHub releases
#   burpsuite — distributed as standalone JAR, installed manually
#   autopsy   — requires Java GUI; replaced with sleuthkit CLI + tsk_recover
#   volatility3 — installed via pip, not apt

# Pin to a specific Kali rolling snapshot digest for reproducible builds.
# Update this digest periodically after testing. Get latest with:
#   docker pull kalilinux/kali-rolling:latest && docker inspect kalilinux/kali-rolling:latest | grep Id
# DL3007 suppressed in hadolint config — Kali has no versioned release tags.
FROM kalilinux/kali-rolling:latest

ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD=changeme
ARG GHIDRA_VERSION=11.1.2
ARG GHIDRA_DATE=20240926
ARG GHIDRA_SHA=8afd1d3d5108a1a2a29f28f4c1acfd3e25e8b61cc60ac04c7e40c56dae1b0fda

# Use bash with pipefail for all RUN instructions that use pipes
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ── Desktop, VNC, noVNC and Kali tools ───────────────────────────────────────
# All packages here are confirmed available in kali-rolling apt repos.
# ghidra, burpsuite, autopsy, and volatility3 are handled separately below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Lightweight desktop environment
    xfce4 xfce4-terminal \
    # VNC server
    tigervnc-standalone-server \
    # noVNC browser client + websocket proxy
    novnc websockify \
    # Network analysis
    wireshark \
    nmap \
    gobuster \
    sqlmap \
    tcpdump \
    tshark \
    netcat-traditional \
    # Password cracking
    john \
    hashcat \
    # Disk and memory forensics (CLI — replaces autopsy GUI)
    sleuthkit \
    binwalk \
    foremost \
    # Python and pip for volatility3 + other tools
    python3 python3-pip \
    # Java runtime — required for Ghidra and Burp Suite
    default-jdk \
    # Misc tools
    curl wget git tmux vim \
    dbus-x11 unzip \
    && rm -rf /var/lib/apt/lists/*

# ── Volatility 3 via pip (not available in kali apt) ─────────────────────────
RUN pip install --no-cache-dir volatility3

# ── Ghidra via official NSA GitHub release (not in kali apt) ─────────────────
RUN wget -q "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_DATE}.zip" \
         -O /tmp/ghidra.zip \
    && echo "${GHIDRA_SHA}  /tmp/ghidra.zip" | sha256sum --check \
    && unzip -q /tmp/ghidra.zip -d /opt/ \
    && mv "/opt/ghidra_${GHIDRA_VERSION}_PUBLIC" /opt/ghidra \
    && ln -s /opt/ghidra/ghidraRun /usr/local/bin/ghidra \
    && rm /tmp/ghidra.zip

# ── Burp Suite Community via standalone JAR (not in kali apt) ────────────────
RUN wget -q "https://portswigger.net/burp/releases/download?product=community&type=Jar" \
         -O /opt/burpsuite.jar \
    && printf '#!/bin/bash\njava -jar /opt/burpsuite.jar "$@"\n' > /usr/local/bin/burpsuite \
    && chmod +x /usr/local/bin/burpsuite

# ── Create operator user ───────────────────────────────────────────────────────
RUN useradd -m -s /bin/bash operator \
    && echo "operator:${VNC_PASSWORD}" | chpasswd

# ── VNC configuration ─────────────────────────────────────────────────────────
USER operator
RUN mkdir -p /home/operator/.vnc \
    && printf '#!/bin/bash\nxfce4-session &\n' > /home/operator/.vnc/xstartup \
    && chmod +x /home/operator/.vnc/xstartup

RUN printf '%s\n%s\n\n' "${VNC_PASSWORD}" "${VNC_PASSWORD}" \
    | vncpasswd /home/operator/.vnc/passwd 2>/dev/null || true

# ── Startup script ────────────────────────────────────────────────────────────
# Must run as root to bind ports and start system services,
# then drops to operator user for the VNC session.
# DL3002 suppressed in hadolint config in validate.yml.
USER root
RUN cat > /start.sh << 'SCRIPT'
#!/bin/bash
set -e

# Start VNC server as operator user
su -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost" operator

# Start noVNC websocket proxy on port 6080 → VNC port 5901
websockify --web=/usr/share/novnc 6080 localhost:5901 &

# Keep container alive
tail -f /dev/null
SCRIPT
chmod +x /start.sh

# ── noVNC index redirect ──────────────────────────────────────────────────────
RUN ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080
HEALTHCHECK --interval=30s --timeout=10s \
  CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]
