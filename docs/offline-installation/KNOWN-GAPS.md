# Known gaps in this offline bundle

## 1. `python:3.12-alpine` is not digest-pinned (pre-existing, not fixed here)

`cei-labs-engine/operator/tcp-gateway/Dockerfile` does `FROM python:3.12-alpine`
with no `@sha256:...` pin — unlike every other Dockerfile in both repos. This
was NOT changed as part of building this bundle (out of scope — the source
repo's pinning is not this bundle's to fix). What's vendored:

- `images/base/python_3.12-alpine_UNPINNED.tar` — whatever `python:3.12-alpine`
  resolved to on 2026-07-22 (digest recorded in `images/MANIFEST.json`).
- `images/custom/tcp-gateway.tar` — the `tcp-gateway` image already built
  FROM that exact resolved base, so the *running* platform is reproducible
  even though the Dockerfile's own `FROM` line is not. If tcp-gateway ever
  needs to be rebuilt from source on the air-gapped machine, it cannot pull
  a fresh `python:3.12-alpine` — it would need `images/base/python_3.12-alpine_UNPINNED.tar`
  loaded and manually retagged to `python:3.12-alpine` first, and even then
  it would be rebuilding from the same fixed base rather than whatever is
  current at rebuild time (which is arguably the safer offline behavior,
  not a worse one).

## 2. Ansible Galaxy collections not vendored

`ansible/site.yml` uses `community.docker.*`, `community.general.*`, and
`ansible.posix.*` modules (docker_swarm, ufw, firewalld, timezone, etc.).
These are Ansible Galaxy collections, not Fedora RPMs — `dnf download`
cannot fetch them. `ansible-core` itself IS vendored (`rpms/`), but running
`ansible-playbook` against `ansible/site.yml` on the air-gapped machine will
fail on missing collections unless they're vendored separately (not done
here — this bundle's `install.sh` does not use Ansible at all; it replicates
the single-host path from `cei-labs-engine/scripts/install.sh` directly in
bash, which needs none of these collections). If you actually need
multi-host Ansible provisioning on the air-gapped network, vendor
`community.docker`, `community.general`, and `ansible.posix` with
`ansible-galaxy collection download` on an internet-connected machine first.

## 3. Docker Compose plugin dependency chain

`docker-compose-plugin` was resolved via `dnf download --resolve --alldeps`
against Docker's official Fedora repo inside a `fedora:44` container, same
as `docker-ce`. Not independently smoke-tested beyond the RPM-set
install-cleanliness check (`docs/VERIFICATION.md`) — `docker compose`
itself was not invoked.

## 4. `dnf download --resolve --alldeps` misses conditional ("if") requires

Found by actually testing the install (not just reading dnf's docs): `dnf
download --resolve --alldeps` follows static `Requires:`, but not rich/
conditional dependencies like `fail2ban-server`'s `Requires: (fail2ban-selinux
if selinux-policy-targeted)` — that conditional only evaluates once
`selinux-policy-targeted` is actually a candidate in the transaction, which
never happened during the initial vendoring pass. Root-caused via a clean
`fedora:44` container install test with `--disablerepo='*'` (see
`docs/VERIFICATION.md`): `fail2ban-selinux` was fetched as a targeted
follow-up once the conflict surfaced, and `rpms/repodata/` was regenerated
with `createrepo_c --update` over the combined set (362 packages total).
`install.sh` was also changed as a result: it installs by **package name
against the local repo index** (`dnf install --repofrompath=local,...
<names>`), not `dnf install *.rpm` — the flat-glob form makes dnf try to
force-install every downloaded alternative-provider package simultaneously
(e.g. both `wget1-wget` and `wget2-wget`, which both provide `wget` and
genuinely conflict) instead of letting its solver choose one, which is
exactly the failure mode this caught. If you re-vendor this RPM set later
with a different top-level package list, re-run the same clean-container
install test before trusting it — this class of gap won't show up just from
reading `dnf download`'s output.

## 5. No real Fedora hardware available for end-to-end testing

Everything below was built/verified on Windows + Docker Desktop (WSL2
backend), never on real Fedora 44 hardware or a real air-gapped network.
See `docs/VERIFICATION.md` for exactly what was and wasn't exercised.
