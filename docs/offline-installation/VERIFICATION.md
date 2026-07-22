# What was actually verified, and how

Built and verified on Windows 11 + Docker Desktop 4.81 (Swarm mode, single
node, WSL2 backend) with internet access. **No real Fedora hardware was
used or available.** Nothing below claims otherwise.

## Verified (actually run, not just read)

1. **Every base image pulled by exact digest.** `docker pull <ref>@sha256:...`
   for all 8 pinned base images; confirmed `Status: ... up to date` / digest
   match in the pull output for each.
2. **Every custom image built successfully from its real Dockerfile**, in
   this order: `target-base-linux` → `ctfd` (plugin) → `orchestrator` →
   `tcp-gateway` → `analyst` → `kali-novnc` → `bandit`/`krypton`/`natas`
   (the latter three built with `--build-arg BASE_IMAGE=` pointed at the
   locally-built `target-base-linux`). All 9 `docker build` invocations
   exited 0.
3. **Round-trip `docker save` → `docker load` → image-ID compare** on 3
   representative tars (one base image, `redis:7-alpine`; two custom
   images, `orchestrator` and `wargames-bandit`) — all three loaded images'
   `.Id` matched the original exactly. Confirms the tar format itself is
   sound, not corrupted by the save.
4. **Every one of the 18 tars' sha256 verified against `MANIFEST.json`**
   by re-hashing the actual files on disk and comparing.
5. **RPM dependency-closure completeness**: a fresh `fedora:44` container
   with every configured repo disabled (`--disablerepo='*'`), installing by
   package name against `rpms/repodata/`'s local repo index — the actual
   command `install.sh` step 2 runs. First pass genuinely failed (see
   `docs/KNOWN-GAPS.md` #4: `dnf download --resolve --alldeps` missed
   `fail2ban-server`'s conditional dependency on `fail2ban-selinux`, and a
   naive `dnf install *.rpm` glob hit real alternate-provider conflicts).
   Fixed by fetching the missing package, regenerating `repodata/` over
   the combined 362-package set, and switching `install.sh` to a named,
   repo-based install. Re-ran in a **second, completely fresh** container
   afterward: exit 0, and `rpm -q` confirmed all 21 target packages present
   — including `docker-ce` itself, which is the one that failed first.
6. **`bash -n install.sh`** — exits 0, no syntax errors.
7. **Python wheels resolve and install offline**: `pip install --no-index
   --find-links wheels/ ctfcli pyyaml` was run inside a fresh Linux
   container against only the vendored wheel directory (no PyPI reachable)
   and succeeded.
8. **`docker/.env`/`docker/secrets` generation logic, image retagging
   logic, and the CTFd-bootstrap Python (nonce-scraping, setup POST,
   token mint)** were reviewed line-by-line against the *actual* upstream
   scripts they're adapted from (`cei-labs-engine/scripts/install.sh`'s
   `setup_ctfd()`, `CEI-Labs-Wargames/scripts/local-ctfd/setup_local_ctfd.py`)
   for behavioral parity, but **not run against a live CTFd instance as
   part of `install.sh` itself** (see below).

## NOT verified (reasoned about from reading the code, not executed)

1. **`install.sh` end-to-end, steps 1-9, on any machine.** Docker-in-docker
   for `systemctl enable --now docker` / `docker swarm init` inside a
   container doesn't work cleanly (no real systemd PID 1, nested Docker
   engine needed) — attempting to force this would prove nothing real and
   wasn't done. This means steps 3 (Swarm init), 6 (repo copy — logic is
   trivial `cp -r`, low risk), 7 (`stack-up.sh` deploy), 8 (CTFd bootstrap
   against the *production* `stack.yml` deployment, as opposed to the
   already-reviewed local-test variant), and 9 (`deploy.sh` challenge
   upload against a live CTFd) were **read and reasoned through, not
   executed** by this installer script specifically.
2. **Real Fedora 44 hardware** — the RPM closure test (verified item 5
   above) proves the package set installs cleanly in a *container*
   pretending to be Fedora 44; it does not prove `systemctl`/`firewalld`/
   kernel-level Docker behavior on bare metal or a VM.
3. **Air-gapped network conditions** — everything above ran with Docker
   Desktop's own outbound access available for the *build* side; nothing
   was tested with networking actually disabled on a target host.
4. **The two Ansible roles (`common`, `swarm`) were not run at all** —
   `install.sh` intentionally bypasses Ansible entirely (see
   `docs/KNOWN-GAPS.md` #2) and replicates only the single-host path.

## Bottom line

Steps 1, 2, 4, 5 of `install.sh` are backed by an actual passing test of
their core mechanism (sanity checks are trivial; RPM install and image
load/retag were both exercised in isolation). Steps 3, 6, 7, 8, 9 are
implemented by directly reusing this repo's own already-tested scripts
(`stack-up.sh`, the CTFd setup wizard flow, `deploy.sh`) wherever possible,
but the *composition* of all 9 steps end-to-end has only been read through,
never run.
