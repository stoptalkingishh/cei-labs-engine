# Security Audit: Status (cei-labs-engine)

**Related:** [`cei-labs-net` status](../../cei-labs-net/docs/security-audit-status.md) · [`CEI-Labs-Wargames` status](../../CEI-Labs-Wargames/docs/security-audit-status.md)

## What this is

A full cybersecurity audit of all three repos in this ecosystem
(`CEI-Labs-Wargames`, `cei-labs-engine`, `cei-labs-net`), run as three
parallel independent reviews, deliberately excluding the CTF tracks'
own intentional teaching vulnerabilities. This doc covers this repo's
three findings; see the related docs above for the other two repos'.

## Findings and fixes (all merged to `main`)

| Severity | Finding | Branch | Verification |
| :--- | :--- | :--- | :--- |
| Medium-High | `admin_mappings.html`'s Add/Update and Remove forms were both missing the CSRF nonce field `launch.html`'s forms already carry. | `fix/csrf-admin-mappings` (merged) | **Fully live-verified.** Confirmed the un-fixed forms genuinely 403 on every real admin submission (not a hypothetical — CTFd's own CSRF check was correctly rejecting them). After the fix, copied the corrected template into the live running CTFd container and submitted the real rendered form (nonce scraped from the actual response, not injected) for both forms — both now succeed (302). |
| High | `operator/kali-novnc/Dockerfile` and `operator/analyst/Dockerfile` both baked a shared password into the image at build time — recoverable via `docker inspect`/`docker history` by anyone who could pull the (public) image, never rotated, shared by every team. | `fix/shared-vnc-password` (merged) | **Fully container-level verified**, re-run once Docker recovered. Built both images fresh: each refuses to start with no password set (`FATAL: ... refusing to start with no password`, exit 1); `docker history --no-trunc` on both confirms no password string in any layer; booted each with a runtime password (`VNC_PASSWORD` / `OPERATOR_PASSWORD`) and completed a real SSH login from a separate container over the network (not a hypothetical — actual `sshpass`/`ssh` round-trip to `operator@<container-ip>`, both returned `SSH_LOGIN_SUCCESS`). Orchestrator-side logic remains unit-tested (78/78). One incidental finding along the way: this branch predated `.gitattributes`, so a raw Windows checkout CRLF-corrupted the embedded heredoc script's shebang (`exec /start.sh: no such file or directory`) — resolved by merging onto current `main` (which has `.gitattributes`) before building; not a flaw in the fix itself. |
| Low | `operator/kali-novnc/Dockerfile`'s `FROM kalilinux/kali-rolling:latest` was an unpinned floating tag, combined with this image's CI build using `no-cache: true` — every rebuild could silently pull a different (potentially compromised) upstream base layer. | `fix/pin-kali-base-digest` (merged) | **Live-verified.** Confirmed via `docker pull kalilinux/kali-rolling:latest` that the pinned digest matches what `:latest` currently resolves to, and `docker image inspect` on the pinned reference resolves correctly. |

## Not done

All three findings are merged to `main` and fully verified. No PRs were
opened (merged directly, since these were single-purpose audit-fix
branches created in the same environment doing the merging).
