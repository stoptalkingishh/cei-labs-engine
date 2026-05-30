# operator/kali-novnc/Dockerfile
# Full Kali Linux desktop accessible via browser (noVNC).
# Used for Sprint 3 PCCC challenges requiring GUI tools:
# Wireshark, Ghidra, NetworkMiner, Burp Suite Community.
# Spawned by CTFd Docker Challenges plugin per participant.

FROM kalilinux/kali-rolling:latest

ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD=changeme

# ── Desktop and noVNC ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Lightweight desktop \
    xfce4 xfce4-terminal \
    # VNC server \
    tigervnc-standalone-server \
    # noVNC browser client \
    novnc websockify \
    # Kali tools relevant to PCCC \
    wireshark \
    ghidra \
    burpsuite \
    nmap \
    gobuster \
    sqlmap \
    john \
    hashcat \
    volatility3 \
    sleuthkit \
    autopsy \
    tshark \
    tcpdump \
    binwalk \
    foremost \
    netcat-traditional \
    python3 python3-pip \
    curl wget git \
    # Terminal and misc \
    tmux vim \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

# ── Create operator user ───────────────────────────────────────────────────────
RUN useradd -m -s /bin/bash operator \
    && echo "operator:${VNC_PASSWORD}" | chpasswd

# ── VNC configuration ─────────────────────────────────────────────────────────
USER operator
RUN mkdir -p /home/operator/.vnc

# Create xstartup file via Here-Document
RUN <<EOF
cat > /home/operator/.vnc/xstartup <<'XSTARTUP'
#!/bin/bash
xfce4-session &
XSTARTUP
chmod +x /home/operator/.vnc/xstartup
EOF

# Set VNC password using non-interactive pipe
RUN echo "${VNC_PASSWORD}" | vncpasswd -f > /home/operator/.vnc/passwd && \
    chmod 600 /home/operator/.vnc/passwd

# ── Startup script ────────────────────────────────────────────────────────────
USER root
# Create startup script via Here-Document
RUN <<EOF
cat > /start.sh <<'STARTUP'
#!/bin/bash
set -e
# Start VNC server
vncserver :1 -geometry 1280x800 -depth 24 -localhost
# Start noVNC websocket proxy
websockify --web=/usr/share/novnc 6080 localhost:5901 &
# Keep container alive
tail -f /dev/null
STARTUP
chmod +x /start.sh
EOF

# ── noVNC index redirect ──────────────────────────────────────────────────────
RUN ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080
HEALTHCHECK --interval=30s --timeout=10s \
  CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]