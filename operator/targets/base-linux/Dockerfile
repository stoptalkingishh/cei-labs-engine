# operator/targets/base-linux
# Minimal target box for target+attacker wargame challenges, spun up on demand
# by the Challenge Instance Orchestrator (docker/orchestrator). Deliberately
# lightweight (debian-slim, not the full ubuntu:24.04 forensics toolchain used
# by operator/analyst) — it exists to be FROM'd by specific challenge images
# in the wargames repo, not run as-is.
#
# A challenge author extends this with whatever the scenario needs (a
# vulnerable service, a planted flag, misconfigured permissions, ...) and
# publishes their own image; the orchestrator takes that image reference at
# instance-creation time (see docker/orchestrator/README.md, "target_image").
FROM debian:12-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    iproute2 \
    netcat-openbsd \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd

# Restore the standard Debian login MOTD. debian:12-slim ships an EMPTY
# /etc/motd and defers MOTD printing to pam_motd, so the familiar
# "programs included with Debian ... ABSOLUTELY NO WARRANTY" text never
# appears at SSH login -- only the per-user CEI banner (added by the
# wargames images via /etc/profile.d) shows. Author the canonical text and
# print it deterministically via sshd (PrintMotd yes), removing pam_motd's
# own motd lines so it isn't printed twice. The CEI profile.d banner is a
# separate mechanism and still shows after this. sshd_config uses the FIRST
# value per keyword, so rewrite any existing (commented) PrintMotd in place
# rather than only appending.
RUN printf '%s\n' \
    'The programs included with the Debian GNU/Linux system are free software;' \
    'the exact distribution terms for each program are described in the' \
    'individual files in /usr/share/doc/*/copyright.' \
    '' \
    'Debian GNU/Linux comes with ABSOLUTELY NO WARRANTY, to the extent' \
    'permitted by applicable law.' \
    > /etc/motd \
 && sed -i 's/^#*[[:space:]]*PrintMotd.*/PrintMotd yes/' /etc/ssh/sshd_config \
 && ( grep -q '^PrintMotd yes' /etc/ssh/sshd_config || printf '\nPrintMotd yes\n' >> /etc/ssh/sshd_config ) \
 && ( [ -f /etc/pam.d/sshd ] && sed -i '/pam_motd/s/^/#/' /etc/pam.d/sshd || true )

# Challenge authors should COPY in their own flag/service setup and override
# CMD as needed; sshd -D is a reasonable default for shell-access challenges.
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D"]
