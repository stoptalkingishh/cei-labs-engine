# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
This repo has 120+ commits predating this file — entries below start from
where this file was introduced (2026-07-15) plus a milestone summary of
what came before it, not a commit-by-commit history. See `git log` for the
full record.

## [Unreleased]

### Added
- `docker/ctfd/plugins/typed-answer-flags`: a fail-closed CTFd custom flag
  type that delegates alias, coordinate-tolerance, identifier-normalization,
  and multipart verification to CTFGenerator pinned at reviewed commit
  `3bf708868d2af1e567686ca3e7e684b537fd0451`; candidate submissions are not
  logged and malformed stored specifications are rejected.
- `docker/ctfd/plugins/submission-history`: let a player review the flags they
  have already submitted, account-scoped, so sharing a solved challenge no
  longer requires a screenshot.
- `ansible/inventory-fedora-live.ini`: live Fedora Swarm inventory template
  targeting the current OPNsense LAN/server network `192.168.10.0/24`
  (gateway `192.168.10.1`), replacing the stale `192.168.1.0/24` /
  five-VLAN reference assumptions. Records the confirmed SSH-reachable
  candidate `192.168.10.192` and the unconfirmed `192.168.10.235` lease
  candidate; SSH user/key are placeholders pending node-identity
  confirmation. Player Wi-Fi (`10.10.32.0/22`) is explicitly out of scope.
- `docker/stack.yml`: commented-out scaffolding (secret definition +
  `orchestrator` service mount) for
  `hint_wallet_sync_secret_previous`, an
  `external: true` Docker secret an operator provisions only for the
  duration of a coordinated `hint_wallet_sync_secret` rotation — the
  application-side support for accepting either secret already existed
  (`HINT_WALLET_SYNC_SECRET_PREVIOUS` in `app/config.py`/`app/main.py`,
  PR #13), this just adds the missing operator-facing piece. Documented as
  a full runbook in `docs/local-testing-deployment.md` ("Rotating
  hint_wallet_sync_secret"). The primary `hint_wallet_sync_secret` itself
  stays `file:`-sourced (unchanged from PR #13) — see that doc section for
  why an `external:` primary secret was considered and rejected now that a
  file-based one is already live in production with real data in it.
- `docs/architecture-decisions.md`: ADR-001 (Docker Swarm, not K3s, is the
  production orchestration platform — resolves the "public repo
  description still says K3s" inconsistency the production-readiness
  tracker flagged) and ADR-002 (written justification for Traefik's
  read-only and the orchestrator's read-write Docker socket mounts, plus a
  recommended not-yet-scheduled follow-up: a docker-socket-proxy to narrow
  the orchestrator's API surface).
- `docs/README.md`: an index separating the living reference docs from the
  dated session logs, so an operator can tell which file to act on.

### Changed
- **Operator-visible:** `ORCHESTRATOR_OFFLINE_MODE` / `ORCHESTRATOR_OFFLINE_HOST`
  (PR #44) collapse the attacker workstation's hostname-based link into a
  single direct-IP noVNC address that works without DNS, instead of a
  primary/fallback pair whose primary is permanently broken offline. Tri-state
  (`auto` by default, decided at startup by probing whether `BASE_DOMAIN`
  resolves) and fails fast when the mode is needed but the host is unset.
- Hint scoring is now **per-challenge percentage reduction with a progression
  window** (PR #35), replacing the shared-currency wallet: a hint costs a
  percentage of that challenge's own score, and hints unlock only within a
  bounded window of the player's progress. Requires the `cost_percent` column
  added by PR #39 (`wallet_unlocks` table migration) on existing deployments.
- Pinned every base/upstream image to an immutable digest instead of a
  floating tag: `traefik:v3.7.6`, `mariadb:10.11`, `redis:7-alpine` in
  `docker/stack.yml`; `ctfd/ctfd:3.8.6` (CTFd Dockerfile base, bumped from
  3.8.2 in PR #42), `python:3.12-slim` (orchestrator Dockerfile base),
  `ubuntu:24.04` (analyst Dockerfile base), `debian:12-slim`
  (target-base-linux Dockerfile base). `kalilinux/kali-rolling` was already
  pinned from the 2026-07 security audit.
- `docker/.env.example`'s `IMAGE_TAG` no longer defaults to `latest` — now
  a placeholder that forces picking an explicit `sha-<commit>` release tag
  (the immutable tag convention `build-ctfd.yml`/`build-orchestrator.yml`
  already produce on every push to `main`).
- Attacker-workstation links now prefer a DNS-free route, and the number of
  distinct scoreboards a player sees is consolidated to one (PR #43).
- Wargame stage challenges hide automatically at their stage boundary rather
  than waiting on a manual admin click (PR #31).

### Fixed
- `target-attacker` challenges served every team the literal
  `__NATAS1_SECRET__` placeholder instead of a generated per-team value
  (PR #48).
- Lost-update race in the orchestrator's pause/reboot/shutdown state
  transitions (PR #24) — a reaper sweep could interleave with a request and
  leave a live container marked stopped.
- Attacker workstation's no-DNS noVNC fallback is now encrypted (PR #23).
- Wargame stage skip: the instance-launcher is gated on CTFd challenge
  visibility (PR #26), and a solved challenge no longer shows "hints aren't
  available" (PR #38).
- kali-novnc: Chromium now starts (`--no-sandbox`; the renderer sandbox needs
  `CLONE_NEWUSER`, which the deliberate `cap_drop: ALL` policy removes) —
  PR #45.
- `base-linux`: the standard Debian MOTD is restored at SSH login (PR #33).
- Hint content renders as Markdown rather than escaped plain text (PR #40);
  tier-cost wording simplified (PR #37); submission history expands inline
  instead of opening a new tab (PR #36); the challenge modal widens on
  desktop (PR #41).

## Milestones before this file existed

- Docker Swarm orchestration stack stood up: Traefik ingress, CTFd +
  MariaDB + Redis, a custom challenge-instance orchestrator (the
  MultiJuicer-equivalent for per-team on-demand containers).
- Trusted-gateway tenant isolation design, verified 42/42 on real Swarm
  hardware (cross-tenant, egress, management-plane, and NET_ADMIN
  route-abuse denial).
- Per-team dynamic flag generation (`secrets.token_urlsafe`/`secrets.
  choice`), rolled out across the instance-launcher plugin.
- Idempotent/concurrency-safe lifecycle operations, verified with real
  20-way concurrent create/relaunch tests on Swarm.
- Encrypted backup + isolated scratch-restore, verified against real data
  (users/teams/challenges/submissions/solves counts reconciled).
- Security audit: fixed a missing CSRF nonce on admin mapping forms, a
  shared VNC/operator password baked into images, and an unpinned Kali
  base image.
- Staggered-game administration (independent per-game starts/scoreboards)
  merged to `main`, plus a trusted-gateway rewrite of `challenge-edge`
  routing, real-Swarm station validation, and CTFd-dialect/launcher-action
  fixes found via adversarial persona testing.

Full detail for all of the above lives in `docs/self-hosted-wargames-status.md`,
`docs/security-audit-status.md`, and `docs/validation-session-2026-07-14-15.md`.
