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
FROM debian:12-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    iproute2 \
    netcat-openbsd \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd

# Challenge authors should COPY in their own flag/service setup and override
# CMD as needed; sshd -D is a reasonable default for shell-access challenges.
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D"]
