FROM ubuntu:24.04

ARG OPERATOR_PASSWORD=TrainingPlatform2025
ARG DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    sudo \
    tmux \
    vim \
    nano \
    less \
    curl \
    wget \
    git \
    tree \
    ripgrep \
    unzip \
    p7zip-full \
    sleuthkit \
    autopsy \
    binwalk \
    foremost \
    testdisk \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    tshark \
    nmap \
    netcat-openbsd \
    tcpdump \
    john \
    hashcat \
    jq \
    xxd \
    strace \
    ltrace \
    gdb \
    binutils \
    file \
    openssl \
    gcc \
    make \
    && rm -rf /var/lib/apt/lists/*

# FIX: Added -g operator to use the existing group instead of attempting to create it
RUN useradd -m -s /bin/bash -g operator operator

RUN echo "operator:${OPERATOR_PASSWORD}" | chpasswd \
    && mkdir -p /etc/sudoers.d \
    && echo "operator ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/operator \
    && chmod 0440 /etc/sudoers.d/operator

USER operator
WORKDIR /home/operator

RUN python3 -m pip install --user --no-cache-dir --break-system-packages \
    volatility3 \
    pwntools \
    capstone \
    yara-python \
    impacket \
    requests \
    tabulate

ENV PATH="/home/operator/.local/bin:${PATH}"
ENV HISTTIMEFORMAT="%d/%m/%y %T "

RUN mkdir -p /home/operator/.local/lib/python3.12/site-packages/volatility3/symbols/ \
    && wget -q "https://downloads.volatilityfoundation.org/volatility3/symbols/windows.zip" \
         -O /tmp/windows-syms.zip \
    && unzip -q /tmp/windows-syms.zip \
         -d /home/operator/.local/lib/python3.12/site-packages/volatility3/symbols/ \
    && rm /tmp/windows-syms.zip \
    && wget -q "https://downloads.volatilityfoundation.org/volatility3/symbols/linux.zip" \
         -O /tmp/linux-syms.zip \
    && unzip -q /tmp/linux-syms.zip \
         -d /home/operator/.local/lib/python3.12/site-packages/volatility3/symbols/ \
    && rm /tmp/linux-syms.zip \
    && mkdir -p /home/operator/cases /home/operator/work \
    && printf '%s\n' \
        "alias vol3='python3 -m volatility3'" \
        'PS1='"'"'\[\033[01;32m\]\u@analyst\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '"'" \
        >> /home/operator/.bashrc \
    && printf '%s\n' \
        'set -g mouse on' \
        'set -g history-limit 10000' \
        'bind | split-window -h' \
        'bind - split-window -v' \
        > /home/operator/.tmux.conf

USER root

RUN mkdir -p /var/run/sshd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && echo "AllowUsers operator" >> /etc/ssh/sshd_config \
    && echo "Banner /etc/ssh/banner" >> /etc/ssh/sshd_config \
    && printf '%s\n' \
        '╔══════════════════════════════════════════════════════════╗' \
        '║          CTF Training Platform  Analyst Node             ║' \
        '║                                                          ║' \
        '║  Tools: vol3  sleuthkit  john  tshark  binwalk           ║' \
        '║         foremost  gdb  pwntools  netcat                  ║' \
        '║                                                          ║' \
        '║  CTFd:  http://ctfd.ctf.local   (submit flags here)      ║' \
        '║  Cases: ~/cases/                (read-only evidence)     ║' \
        '║  Work:  ~/work/                 (your scratch space)     ║' \
        '╚══════════════════════════════════════════════════════════╝' \
        > /etc/ssh/banner

EXPOSE 22
HEALTHCHECK --interval=30s --timeout=5s \
  CMD nc -z localhost 22 || exit 1
CMD ["/usr/sbin/sshd", "-D"]