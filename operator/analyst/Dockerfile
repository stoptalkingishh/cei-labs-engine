# operator/analyst/Dockerfile
# SSH-accessible analyst workstation container.
# One pod per participant in the analyst namespace.
# Volatility 3 symbol tables pre-cached — eliminates msdl.microsoft.com
# fetches that failed under event load in the LKG AAR.

FROM ubuntu:24.04

ARG OPERATOR_PASSWORD=changeme
ARG DEBIAN_FRONTEND=noninteractive

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    tmux vim nano less curl wget \
    # Forensics \
    sleuthkit autopsy binwalk foremost testdisk \
    # Python + pip \
    python3 python3-pip python3-venv python3-dev \
    # Network \
    tshark nmap netcat-openbsd tcpdump \
    # Password tools \
    john hashcat \
    # Scripting \
    jq xxd strace ltrace gdb binutils file \
    # Crypto \
    openssl \
    # Build tools (needed for some PCCC challenge wrappers) \
    gcc make \
    # Misc \
    sudo git tree ripgrep unzip p7zip-full \
    && rm -rf /var/lib/apt/lists/*

# ── Create operator user ───────────────────────────────────────────────────────
# FIXED: Added line continuation backslash after the pipe to unify user provisioning execution bounds cleanly
RUN useradd -m -s /bin/bash -G sudo operator \
    && echo "operator:${OPERATOR_PASSWORD}" | \
    chpasswd \
    && echo "operator ALL=(ALL) NOPASSWD: /usr/bin/vol3" >> /etc/sudoers.d/operator

# ── Python tools ──────────────────────────────────────────────────────────────
USER operator
WORKDIR /home/operator

RUN python3 -m pip install --user --break-system-packages \
    volatility3 \
    pwntools \
    capstone \
    yara-python \
    impacket \
    requests \
    tabulate \
    && python3 -m pip cache purge

ENV PATH="/home/operator/.local/bin:${PATH}"

# ── Volatility 3 symbol cache ─────────────────────────────────────────────────
# Pre-cache Windows and Linux symbol bundles at BUILD TIME.
# This eliminates the msdl.microsoft.com fetch at event time that caused
# complete plugin failures under real event load in the LKG AAR.
RUN mkdir -p /home/operator/.local/lib/python3.12/site-packages/volatility3/symbols/

RUN wget -q "https://downloads.volatilityfoundation.org/volatility3/symbols/windows.zip" \
         -O /tmp/windows-syms.zip \
    && unzip -q /tmp/windows-syms.zip \
         -d /home/operator/.local/lib/python3.12/site-packages/volatility3/symbols/ \
    && rm /tmp/windows-syms.zip

RUN wget -q "https://downloads.volatilityfoundation.org/volatility3/symbols/linux.zip" \
         -O /tmp/linux-syms.zip \
    && unzip -q /tmp/linux-syms.zip \
         -d /home/operator/.local/lib/python3.12/site-packages/volatility3/symbols/ \
    && rm /tmp/linux-syms.zip

# ── SSH configuration ─────────────────────────────────────────────────────────
USER root
# FIXED: Unified broken multiline sed operations to single statements to prevent runtime config drops
RUN mkdir /var/run/sshd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && echo "AllowUsers operator" >> /etc/ssh/sshd_config \
    && echo "Banner /etc/ssh/banner" >> /etc/ssh/sshd_config

# ── Welcome banner ────────────────────────────────────────────────────────────
RUN printf '%s\n' \
    '╔══════════════════════════════════════════════════════════╗' \
    '║         CTF Training Platform — Analyst Node            ║' \
    '║                                                          ║' \
    '║  Tools: vol3  sleuthkit  john  tshark  binwalk          ║' \
    '║         foremost  gdb  pwntools  netcat                  ║' \
    '║                                                          ║' \
    '║  CTFd:  http://ctfd.ctf.local   (submit flags here)    ║' \
    '║  Cases: ~/cases/                (read-only evidence)    ║' \
    '║  Work:  ~/work/                 (your scratch space)    ║' \
    '║                                                          ║' \
    '║  Type: tools   for quick command reference              ║' \
    '╚══════════════════════════════════════════════════════════╝' \
    > /etc/ssh/banner

# ── Shell config ──────────────────────────────────────────────────────────────
USER operator
RUN cat >> /home/operator/.bashrc << 'BASHRC'
alias vol3="python3 -m volatility3"
alias tools='echo "
Quick Reference:
  vol3 -f ~/cases/<dump> windows.info
  vol3 -f ~/cases/<dump> windows.pslist
  vol3 -f ~/cases/<dump> linux.bash
  fls -r ~/cases/<image> | grep <filename>
  icat ~/cases/<image> <inode>
  john --wordlist=/usr/share/wordlists/rockyou.txt <hashfile>
  tshark -r ~/cases/<pcap> -Y http
  binwalk -e ~/cases/<file>
"'
PS1='\[\033[01;32m\]\u@analyst\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
export HISTTIMEFORMAT="%d/%m/%y %T "
BASHRC

RUN cat > /home/operator/.tmux.conf << 'TMUX'
set -g mouse on
set -g history-limit 10000
bind | split-window -h
bind - split-window -v
TMUX

RUN mkdir -p /home/operator/cases /home/operator/work

USER root
EXPOSE 22
HEALTHCHECK --interval=30s --timeout=5s \
  CMD nc -z localhost 22 || exit 1
CMD ["/usr/sbin/sshd", "-D"]