# Security Audit: Status (cei-labs-engine)

**Related:** [`cei-labs-net` status](../../cei-labs-net/docs/security-audit-status.md) · [`CEI-Labs-Wargames` status](../../CEI-Labs-Wargames/docs/security-audit-status.md)

## What this is

A full cybersecurity audit of all three repos in this ecosystem
(`CEI-Labs-Wargames`, `cei-labs-engine`, `cei-labs-net`), run as three
parallel independent reviews, deliberately excluding the CTF tracks'
own intentional teaching vulnerabilities. This doc covers this repo's
three findings; see the related docs above for the other two repos'.

## Findings and fixes (all on separate, unmerged branches — nothing here touches `main`)

| Severity | Finding | Branch | Verification |
| :--- | :--- | :--- | :--- |
| Medium-High | `admin_mappings.html`'s Add/Update and Remove forms were both missing the CSRF nonce field `launch.html`'s forms already carry. | `fix/csrf-admin-mappings` | **Fully live-verified.** Confirmed the un-fixed forms genuinely 403 on every real admin submission (not a hypothetical — CTFd's own CSRF check was correctly rejecting them). After the fix, copied the corrected template into the live running CTFd container and submitted the real rendered form (nonce scraped from the actual response, not injected) for both forms — both now succeed (302). This one needs no further verification. |
| High | `operator/kali-novnc/Dockerfile` and `operator/analyst/Dockerfile` both baked a shared password into the image at build time — recoverable via `docker inspect`/`docker history` by anyone who could pull the (public) image, never rotated, shared by every team. | `fix/shared-vnc-password` | Orchestrator-side Python logic (random per-range password generation, env-var injection, surfaced to the player) is unit-tested — 78/78 orchestrator tests pass, including two new tests specifically asserting the password is random per range and reaches the service's real runtime environment under both possible variable names. **Container-level verification (build both images, confirm they refuse to start with no password, confirm SSH login works with a real runtime-supplied one) is NOT yet done** — blocked mid-session by this environment's Docker Desktop/WSL2 build layer degrading badly (confirmed independently of this specific change: a plain `apt-get install openssh-server` with no other packages hung indefinitely with zero progress, and even `docker kill`/`docker exec` on unrelated containers started hanging). Exact re-verification steps are in the PR/branch description and in the session's own continuity notes — this is the top priority to re-run once Docker is healthy again. |
| Low | `operator/kali-novnc/Dockerfile`'s `FROM kalilinux/kali-rolling:latest` was an unpinned floating tag, combined with this image's CI build using `no-cache: true` — every rebuild could silently pull a different (potentially compromised) upstream base layer. | `fix/pin-kali-base-digest` | **Live-verified.** Confirmed via `docker pull kalilinux/kali-rolling:latest` that the pinned digest matches what `:latest` currently resolves to, and `docker image inspect` on the pinned reference resolves correctly. This one needs no further verification. |

## What's still open

**Re-run container-level verification of `fix/shared-vnc-password`** once
Docker is healthy: build both `operator/kali-novnc` and
`operator/analyst`, confirm each refuses to start with no password set
(should log the `FATAL: ... refusing to start with no password`
message and exit), confirm a real SSH login succeeds when a runtime
password is supplied via `-e VNC_PASSWORD=...` / `-e
OPERATOR_PASSWORD=...`, and confirm `docker history --no-trunc` on the
built image shows no password anywhere in any layer. If all three pass,
this branch is fully verified and safe to merge whenever ready.

## Not done

`main` untouched — nothing merged. No PRs opened.
