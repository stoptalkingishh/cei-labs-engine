FROM kalilinux/kali-rolling:latest

ARG DEBIAN_FRONTEND=noninteractive
ARG VNC_PASSWORD=changeme
ARG GHIDRA_VERSION=11.1.2
ARG GHIDRA_DATE=20240926

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
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages volatility3

RUN wget -q "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_DATE}.zip" \
         -O /tmp/ghidra.zip \
    && unzip -q /tmp/ghidra.zip -d /opt/ \
    && mv "/opt/ghidra_${GHIDRA_VERSION}_PUBLIC" /opt/ghidra \
    && ln -s /opt/ghidra/ghidraRun /usr/local/bin/ghidra \
    && rm /tmp/ghidra.zip

RUN wget -q "https://portswigger.net/burp/releases/download?product=community&type=Jar" \
         -O /opt/burpsuite.jar \
    && printf '#!/bin/bash\njava -jar /opt/burpsuite.jar "$@"\n' > /usr/local/bin/burpsuite \
    && chmod +x /usr/local/bin/burpsuite

RUN useradd -m -s /bin/bash operator \
    && echo "operator:${VNC_PASSWORD}" | chpasswd

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
