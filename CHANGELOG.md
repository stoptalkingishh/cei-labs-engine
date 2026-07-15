# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
This repo has 120+ commits predating this file — entries below start from
where this file was introduced (2026-07-15) plus a milestone summary of
what came before it, not a commit-by-commit history. See `git log` for the
full record.

## [Unreleased]

### Changed
- Pinned every base/upstream image to an immutable digest instead of a
  floating tag: `traefik:v3.7.6`, `mariadb:10.11`, `redis:7-alpine` in
  `docker/stack.yml`; `ctfd/ctfd:3.8.2` (CTFd Dockerfile base),
  `python:3.12-slim` (orchestrator Dockerfile base), `ubuntu:24.04`
  (analyst Dockerfile base), `debian:12-slim` (target-base-linux Dockerfile
  base). `kalilinux/kali-rolling` was already pinned from the 2026-07
  security audit.
- `docker/.env.example`'s `IMAGE_TAG` no longer defaults to `latest` — now
  a placeholder that forces picking an explicit `sha-<commit>` release tag
  (the immutable tag convention `build-ctfd.yml`/`build-orchestrator.yml`
  already produce on every push to `main`).

### Added
- `docs/architecture-decisions.md`: ADR-001 (Docker Swarm, not K3s, is the
  production orchestration platform — resolves the "public repo
  description still says K3s" inconsistency the production-readiness
  tracker flagged) and ADR-002 (written justification for Traefik's
  read-only and the orchestrator's read-write Docker socket mounts, plus a
  recommended not-yet-scheduled follow-up: a docker-socket-proxy to narrow
  the orchestrator's API surface).

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
