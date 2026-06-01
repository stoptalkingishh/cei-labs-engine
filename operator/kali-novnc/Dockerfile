FROM kalilinux/kali-rolling:latest

ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD=TrainingPlatform2025

ENV VNC_PASSWORD=${VNC_PASSWORD}

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
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages volatility3

# FIXED: Combined user creation + password into ONE RUN to prevent build failures
RUN useradd -m -s /bin/bash operator \
    && echo "operator:${VNC_PASSWORD}" | chpasswd

USER operator

RUN mkdir -p /home/operator/.vnc \
    && printf '#!/bin/bash\nxfce4-session &\n' > /home/operator/.vnc/xstartup \
    && chmod +x /home/operator/.vnc/xstartup

RUN printf '%s\n%s\n\n' "${VNC_PASSWORD}" "${VNC_PASSWORD}" \
    | vncpasswd /home/operator/.vnc/passwd 2>/dev/null || true

USER root

RUN printf '#!/bin/bash\n\
set -e\n\
su operator -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost no"\n\
websockify --web=/usr/share/novnc 6080 localhost:5901 &\n\
tail -f /dev/null\n' > /start.sh

RUN chmod +x /start.sh \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080

HEALTHCHECK --interval=30s --timeout=10s \
  CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]