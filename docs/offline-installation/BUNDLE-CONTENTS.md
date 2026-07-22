# D:\REPO bundle contents

Built 2026-07-22 on a Windows machine with internet + Docker Desktop
(Swarm mode, single node), for transfer to an air-gapped Fedora 44 machine.

```
REPO/
├── install.sh              # run this on the target Fedora machine
├── docs/
│   ├── BUNDLE-CONTENTS.md  # this file
│   ├── KNOWN-GAPS.md       # the one deliberate pinning gap + other caveats
│   └── VERIFICATION.md     # what was actually verified vs. only read/reasoned about
├── images/
│   ├── MANIFEST.json       # every tar: ref/tag, sha256, byte size
│   ├── base/                (9 tars — pulled by digest, except the 1 noted gap)
│   └── custom/               (9 tars — built here from source Dockerfiles)
├── rpms/                    Fedora 44 RPMs + full dependency closure
├── wheels/                   Python wheels for ctfcli/pyyaml (+ orchestrator/
│                              plugin deps, vendored for reference — already
│                              baked into their images, see below)
└── repos/                    working-tree + .git copies of all 4 source repos
    ├── cei-labs-engine
    ├── CEI-Labs-Wargames
    ├── cei-labs-net           (reference only, not deployed)
    └── cei-labs-event         (reference only, not deployed)
```

## Images

### Base (pulled by exact digest from stack.yml / Dockerfile FROM lines)

| tar | ref |
|---|---|
| mariadb_10.11.tar | mariadb:10.11@sha256:be981e41... |
| redis_7-alpine.tar | redis:7-alpine@sha256:6ab0b6e7... |
| traefik_v3.7.6.tar | traefik:v3.7.6@sha256:21a3d836... |
| ctfd_3.8.2.tar | ctfd/ctfd:3.8.2@sha256:870e396f... |
| debian_12-slim.tar | debian:12-slim@sha256:7b140f37... |
| kali-rolling.tar | kalilinux/kali-rolling@sha256:776d57c9... |
| python_3.12-slim.tar | python:3.12-slim@sha256:c3d81d25... |
| ubuntu_24.04.tar | ubuntu:24.04@sha256:4fbb8e6a... |
| python_3.12-alpine_UNPINNED.tar | python:3.12-alpine (unpinned — see KNOWN-GAPS.md) |

### Custom (built here from each Dockerfile, since the target has no internet to `apt-get install` during its own `docker build`)

| tar | built from | tagged as (source) | retagged at install to |
|---|---|---|---|
| target-base-linux.tar | cei-labs-engine/operator/targets/base-linux | .../target-base-linux:latest | (used as build base only, not redeployed) |
| ctfd-plugin.tar | cei-labs-engine/docker/ctfd | .../ctfd:offline | matches `IMAGE_TAG=offline` in docker/.env |
| orchestrator.tar | cei-labs-engine/docker/orchestrator | .../orchestrator:offline | matches `IMAGE_TAG=offline` |
| tcp-gateway.tar | cei-labs-engine/operator/tcp-gateway | .../tcp-gateway:offline | matches `IMAGE_TAG=offline` |
| analyst.tar | cei-labs-engine/operator/analyst | .../analyst:offline | .../ctf-analyst:offline and :latest |
| kali-novnc.tar | cei-labs-engine/operator/kali-novnc | .../kali-novnc:offline | .../ctf-kali-novnc:offline and :latest |
| wargames-bandit.tar | CEI-Labs-Wargames/targets/bandit | .../bandit:offline | .../bandit-target:latest |
| wargames-krypton.tar | CEI-Labs-Wargames/targets/krypton | .../krypton:offline | .../krypton-target:latest |
| wargames-natas.tar | CEI-Labs-Wargames/targets/natas | .../natas:offline | .../natas-target:latest |

The wargames Dockerfiles' `ARG BASE_IMAGE` defaults to a `ghcr.io/...` ref
that was never published — they were built here with
`--build-arg BASE_IMAGE=ghcr.io/stoptalkingishh/cei-labs-engine/target-base-linux:latest`
pointed at the locally-built base-linux image instead.

## Why retag instead of edit `install.sh`'s image-name assumptions to match

`stack.yml`/`spawn-workspaces.sh` expect `ghcr.io/<org>/cei-labs-engine/<name>:${IMAGE_TAG}`;
the Wargames `build_bandit.py`/`build_krypton.py`/`build_natas.py` scripts
hardcode `.../bandit-target:latest` etc. (env-var overridable, but default
to `:latest`). Rather than pass extra `BANDIT_IMAGE=...` env vars through
every invocation, `install.sh` tags each loaded image under every name
anything downstream might look for — `docker tag` is a pointer, not a copy,
so this costs no extra disk.

## Why repos are copied into `/opt/cei-labs`, not run in place from the drive

`docker/.env`, `docker/secrets/`, and ctfcli's `.ctf/config` all get written
during install — writing those onto the USB drive would mean the platform's
live secrets and config live on removable media. `install.sh` copies
`repos/*` to `$INSTALL_ROOT` (default `/opt/cei-labs`) and operates there;
the drive itself is only ever read after that point.
