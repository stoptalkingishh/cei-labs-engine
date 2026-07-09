# Self-Hosted Wargames: Status (cei-labs-engine)

**Branch:** `feature/self-hosted-wargames-base-images` @ `8e0ceb9` (not merged
to `main` @ `8ff9c7f`)
**Related:** [`CEI-Labs-Wargames` status](../../CEI-Labs-Wargames/docs/self-hosted-wargames-status.md) · [`cei-labs-net` status](../../cei-labs-net/docs/self-hosted-wargames-status.md)

## What this is

`CEI-Labs-Wargames`' self-hosted Bandit/Krypton/Natas migration (see that
repo's status doc) runs on this repo's orchestrator and CTFd plugin. This
branch is the platform-side work that migration needed and surfaced.

## What changed

**`operator/kali-novnc`** (the Natas range's shared attacker image):
- Added `openssh-server` + a TigerVNC XDG-path fix (`~/.config/tigervnc/`,
  not the legacy `~/.vnc/`, which this TigerVNC version tries and fails to
  auto-migrate from) — earlier work in this branch's history.
- Added web-exploitation tooling: `ffuf`, `mitmproxy`, `nano`, `ncat`,
  `socat`, `python3-requests` explicitly.
- Fixed `websockify` (the noVNC proxy) running as root with no reason to —
  only `vncserver` was ever wrapped in `su operator`. Both are now.

**`docker/orchestrator`** — two real fixes plus the Phase 6 hardening pass:
1. **`target-attacker` range attackers had no reachable SSH, ever.**
   `plan_range_attacker()` was called with no port allocator at all (unlike
   `plan_single_target`), so the attacker only ever got Traefik/noVNC
   routing. Now takes an `allocated_port` from the same pool `single-target`
   already uses and publishes it alongside the existing Traefik labels.
   Verified with a real `ssh` connection from outside every container
   involved, not just code review.
2. **`ServiceSpec` gained `cap_drop`/`cap_add`/`read_only`/`cpu_limit_nanos`**,
   wired into `docker_client.create_service()`. `cap_drop=["ALL"]` + a
   narrow, per-instance-type `cap_add` is now applied to every instance
   type this project's own images use (`single-target`, `target-attacker`'s
   target and attacker). `web-app` (Juice Shop) untouched — wasn't part of
   this migration. Checked docker-py 7.1.0's actual source first:
   `no_new_privileges` and `pids_limit` have no equivalent in this SDK
   version's Swarm services API at all, so neither is implemented — noted
   plainly, not silently skipped. `read_only` is wired through but not
   enabled by default anywhere yet (Natas bundles MariaDB in the same
   container, needs a per-image writable-path audit this pass didn't do).
   Every capability actually needed was found by testing, not guessed —
   including one genuine surprise, `SYS_CHROOT`: without it, sshd's
   privilege-separation preauth child fails outright
   (`chroot("/run/sshd"): Operation not permitted`), breaking every SSH
   connection before authentication even starts.

**`docker/ctfd/plugins/instance-launcher`**:
- Fixed a pre-existing CSRF bug: `launch.html`'s Reboot/Relaunch/Extend POST
  forms had no CSRF nonce field, so CTFd's global CSRF check 403'd every one
  of those actions for every player. Found while testing the SSH fix above.
- (Earlier in this branch's history) `/admin/mappings/sync` had the same
  class of CSRF gap, fixed with `@bypass_csrf_protection`.

## Live-verified, not just written

Rebuilt every changed image, redeployed a real local Swarm stack, and
confirmed: a real outside-the-container SSH connection to a range attacker
works and can reach its target; a port released on teardown gets correctly
reused (not leaked); Reboot/Relaunch/Extend return 200 instead of 403; every
privileged mechanism across all four affected images still works under the
new capability restrictions (Bandit's SUID escalation and root-cron-to-`su`
privilege drop, Krypton's SSH, Natas's MPM-ITK per-vhost identity switch and
SQLi-against-MariaDB, kali-novnc's `nmap -sS`/`tcpdump`); `instance_group`
sharing, `shutdown_on_solve` timing (confirmed via the reaper's own log
showing a real scheduled shutdown actually completing), CTFd/orchestrator
network isolation, and inbound-port discipline.

## Known open items (found, not fixed — explicitly out of scope for this pass)

- **The orchestrator's instance/range state is a pure in-memory dict, no
  reconciliation against actual Docker state.** Restarting the orchestrator
  process (or any out-of-band manual Docker surgery) leaves it reporting
  instances that no longer exist, or believing ports are free that are
  actually still bound — hit repeatedly during this session's own iterative
  testing. A real production deployment restarts the orchestrator rarely,
  so this is lower-severity than it sounds, but it's a real gap. Worth a
  "verify + repair on request" reconciliation pass someday.
- **`read_only` rootfs (Phase 6) isn't enabled anywhere yet** — see above.
- **`no_new_privileges`/`pids_limit` aren't implemented at all** — SDK
  limitation in docker-py 7.1.0's Swarm services API, not a choice.
- **Docker Desktop's `internal: true` networks don't fully block outbound
  connectivity in this local test environment** (confirmed via a real `nc`
  connection succeeding from inside a supposedly airgapped network) — a
  known Docker Desktop/WSL2 limitation, not a `cei-labs-net`/orchestrator
  config bug. The CTFd/orchestrator-reachability side of isolation (the
  part that actually matters for keeping teams from touching each other's
  or the platform's infrastructure) is confirmed working. The full outbound
  claim needs re-verification on the real target deployment platform (a
  native Linux Swarm cluster, matching `cei-labs-net`'s actual intended
  architecture) before being considered proven for production.
- `launch.html`'s connect-string display always shows `operator@` regardless
  of the actual instance's username (cosmetic — Bandit/Krypton's real
  connect port/host are correct, just the example username in the display
  text is wrong for anything but the analyst/attacker images). Not fixed
  in this pass.

## Not done at all

No PRs opened, nothing merged to `main`.
