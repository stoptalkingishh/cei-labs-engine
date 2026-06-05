FROM kalilinux/kali-rolling:latest

ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Validate that the argument is passed dynamically and fail build early if empty
RUN test -n "$VNC_PASSWORD"

ENV VNC_PASSWORD=${VNC_PASSWORD}

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

RUN pip install --no-cache-dir --break-system-packages volatility3

# Verify vncpasswd exists after installation
RUN command -v vncpasswd

# Create operator user
RUN useradd -m -s /bin/bash --no-log-init --no-user-group operator

RUN echo "operator:${VNC_PASSWORD}" | chpasswd

USER operator

RUN mkdir -p /home/operator/.vnc \
    && printf '#!/bin/bash\nxfce4-session &\n' > /home/operator/.vnc/xstartup \
    && chmod +x /home/operator/.vnc/xstartup

# Generate VNC password and fail if creation does not succeed
RUN command -v vncpasswd && \
    printf '%s\n%s\n\n' "${VNC_PASSWORD}" "${VNC_PASSWORD}" \
    | vncpasswd /home/operator/.vnc/passwd && \
    test -f /home/operator/.vnc/passwd

USER root

RUN printf '#!/bin/bash\nset -e\nsu operator -c "vncserver :1 -geometry 1280x800 -depth 24 -localhost no"\nwebsockify --web=/usr/share/novnc 6080 localhost:5901 &\ntail -f /dev/null\n' > /start.sh \
    && chmod +x /start.sh \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080

HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:6080/ || exit 1

CMD ["/start.sh"]