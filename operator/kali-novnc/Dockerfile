FROM kalilinux/kali-rolling:latest

ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD=TrainingPlatform2025

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

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
    burpsuite \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages volatility3

RUN id operator 2>/dev/null || useradd -m -s /bin/bash operator

RUN echo "operator:${VNC_PASSWORD}" | chpasswd

USER operator

RUN mkdir -p /home/operator/.vnc \
    && printf '#!/bin/bash\nxfce4-session &\n' > /home/operator/.vnc/xstartup \
    && chmod +x /home/operator/.vnc/xstartup

RUN printf '%s\n%s\n\n' "${VNC_PASSWORD}" "${VNC_PASSWORD}" \
    | vncpasswd /home/operator/.vnc/passwd 2>/dev/null || true

USER root

RUN cat > /start.sh << 'SCRIPT'
#!/bin/bash
set -e
su -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost" operator
websockify --web=/usr/share/novnc 6080 localhost:5901 &
tail -f /dev/null
SCRIPT

RUN chmod +x /start.sh \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080
HEALTHCHECK --interval=30s --timeout=10s \
  CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]