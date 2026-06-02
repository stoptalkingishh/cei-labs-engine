# Dockerfile for Kali Linux + noVNC Environment
FROM kalilinux/kali-rolling:latest

# Set environment variables for non-interactive installs
ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD=TrainingPlatform2025

ENV VNC_PASSWORD=${VNC_PASSWORD}

# Ensure bash is used for all RUN commands
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install base packages and security tools
# Combined into one RUN block to reduce image layers
RUN apt-get update && apt-get install -y --no-install-recommends \
    xfce4 \
    xfce4-terminal \
    tigervnc-standalone-server \
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
    tshark \
    tcpdump \
    python3 \
    python3-pip \
    default-jdk \
    curl \
    wget \
    git \
    tmux \
    vim \
    dbus-x11 \
    unzip \
    ghidra \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install volatility3 using pip
RUN pip install --no-cache-dir --break-system-packages volatility3

# Configure operator user
RUN useradd -m -s /bin/bash --no-log-init operator \
    && echo "operator:${VNC_PASSWORD}" | chpasswd

# Switch to operator to configure VNC
USER operator

RUN mkdir -p /home/operator/.vnc \
    && printf '#!/bin/bash\nxfce4-session &\n' > /home/operator/.vnc/xstartup \
    && chmod +x /home/operator/.vnc/xstartup \
    && printf '%s\n%s\n\n' "${VNC_PASSWORD}" "${VNC_PASSWORD}" \
    | vncpasswd /home/operator/.vnc/passwd 2>/dev/null || true

# Switch back to root to finalize configuration
USER root

# Create start script and handle symlinks
RUN printf '#!/bin/bash\n\
set -e\n\
su operator -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost no"\n\
websockify --web=/usr/share/novnc 6080 localhost:5901 &\n\
tail -f /dev/null\n' > /start.sh \
    && chmod +x /start.sh \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# Expose noVNC port
EXPOSE 6080

# Healthcheck to ensure the service is responsive
HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:6080/ || exit 1

# Default command to run
CMD ["/start.sh"]