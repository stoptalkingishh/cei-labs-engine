# Self-Hosted Wargames: Status (cei-labs-engine)

**Status:** Merged to `main` (confirmed via `git merge-base --is-ancestor
2ffb619 main`). Originally written from `feature/self-hosted-wargames-base-
images` @ `2ffb619` before that branch landed.
**Related:** [`CEI-Labs-Wargames` status](https://github.com/stoptalkingishh/CEI-Labs-Wargames/blob/main/docs/self-hosted-wargames-status.md) · [`cei-labs-net` status](https://github.com/stoptalkingishh/cei-labs-net/blob/main/docs/self-hosted-wargames-status.md)

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

## Player-experience follow-up work (in progress)

A real playtest through the live CTFd UI (not scripted testing) surfaced 8
player-experience gaps, captured in
`docs/instance-launcher-ux-followups.md` and now being worked through a
6-stage plan. **Stage 1 (JSON API for the launch UI) is done and
live-verified:** `docker/ctfd/plugins/instance-launcher/routes.py` gained
`GET /api/status/<challenge_id>` (pure read, safe to poll) and
`POST /api/launch/<challenge_id>` (same default/reboot/relaunch/extend
actions as the existing HTML form, JSON in/out instead), sharing logic with
the original `/launch/<id>` route via two extracted helpers
(`_resolve_config`, `_run_action`) rather than duplicating it. The original
HTML route is untouched and still works as a fallback.

Verified for real against the running stack, with a real session + CSRF
token, not just written: status before any instance exists, launch,
status reflecting the real instance immediately after, reboot, extend
(both the success path and its real "nothing scheduled" error path),
relaunch (see the flaky race noted below), and the 404/`has_environment:
false` paths for a challenge with no environment mapping at all. While
testing this, also hit and correctly diagnosed a live instance of the
already-documented in-memory-store-vs-real-Docker-state desync (a stray
`chinst-2-group-bandit` service with no matching store entry) — cleaned up
by removing the orphaned service/network and letting the real API recreate
it in sync, not by working around the symptom.

**Stage 2 (inject the launch control into CTFd's own challenge modal) is
done, but only partially verified — see the caveat below.**
`docker/ctfd/plugins/instance-launcher/assets/challenge-launch.js` (new)
is registered site-wide via `register_plugin_script`/
`register_plugin_assets_directory` in `__init__.py`. Real reconnaissance
against the live theme (not memory/guessing) found: CTFd's core theme
(Alpine.js-based) renders the *entire* challenge modal body via
`x-html="$store.challenge.data.view"` on `#challenge-window`, replacing it
wholesale on every single challenge open — including re-opening the same
challenge — and the common "click a challenge on the board" path never
dispatches the `load-challenge` window event some other paths use, so a
`MutationObserver` on `#challenge-window` is the only hook that reliably
fires on every render. The script anchors on `#challenge-id` (a hidden
input CTFd itself always renders) and inserts its panel after
`.challenge-desc`, and calls the two Stage-1 JSON routes via `CTFd.fetch()`
(CTFd's own global fetch wrapper, confirmed via the live bundle to
auto-attach the same `CSRF-Token` header CTFd's bundled frontend uses, so
this doesn't scrape a nonce itself).

Caught one real bug this way before it shipped: `register_plugin_script`
takes only a URL (it reads Flask's `current_app` internally) — the first
attempt passed `app` as well, which crashed the whole CTFd process on
every worker (`TypeError: register_script() takes 1 positional argument
but 2 were given`, visible in `docker service logs`). Fixed, rebuilt,
confirmed the service is stable again.

**What's verified:** the script is served with the right content-type,
the challenges page's actual rendered HTML includes the
`<script defer src=".../challenge-launch.js">` tag, `defer` execution
timing is compatible with the script's own `DOMContentLoaded` listener
(deferred scripts always run before that event fires, so the listener is
guaranteed to still be pending when it does), `#challenge-window` is
confirmed present in the live page at load time, and a real
`GET /api/v1/challenges/<id>` response was checked directly to confirm its
`view` HTML actually contains both anchors the script depends on
(`.challenge-desc`, `#challenge-id`) — not just the static template
source.

**Update: confirmed live by the user in a real browser** (this
environment still has no browser tooling of its own) — the panel
renders and works, and that real usage is what surfaced the round-2
feedback above (panel too verbose, launcher repeated on every level).
Stage 2 is now fully done, not just evidenced.

**Stage 3 (Natas noVNC direct-port fallback, no DNS needed) is done and
fully live-verified.** `ServiceSpec.published_port` (a single optional
tuple) generalized to `published_ports: list[tuple[int, int]]` — needed
since the range attacker now publishes two ports (SSH + noVNC) on one
service, not just one. `docker_client.create_service()`,
`controller.py`'s teardown/port-release paths (now release every
published port, not just the first), and `plan_single_target`/
`plan_range_attacker` in `instance_types.py` all updated; all 77
orchestrator tests updated and passing. `plan_range_attacker()` now takes
a second `allocated_novnc_port`, published to noVNC's existing 6080
listener alongside the existing SSH port — mirrors the earlier SSH fix
exactly, for the identical reason (the DNS-based Traefik route the
attacker also gets is simply unreachable with no wildcard DNS available at
all). New `access` fields: `novnc_port`, `novnc_url`, `novnc_note`,
surfaced in both `launch.html` and `challenge-launch.js`.

Live-verified for real, not just curled-and-assumed: launched a real Natas
range, waited for the attacker container to actually reach Running state,
then hit `http://localhost:<novnc_port>/vnc.html` directly with no DNS
involved at all and got back real noVNC page HTML (confirmed by content,
not just a 200), and independently confirmed the SSH port on the same
service still returns a real `SSH-2.0-OpenSSH_10.3` banner via a raw TCP
connect — both published ports work correctly on one service. Also hit,
diagnosed, and worked around two more instances of already-documented
environment quirks while verifying this: the in-memory-store-vs-Docker
desync again (a stale `chinst-2-group-bandit` from earlier Stage 1/2
testing collided with the natas range's fresh port allocation) and the
Swarm image-tag-shadowing issue (the range attacker's `attacker_image`
config still pointed at the real `ghcr.io/stoptalkingishh/...` path,
resynced to the `ghcr.io/local-test/...` build via the plugin's own
`/admin/mappings/sync` endpoint) — neither is new, both already known and
documented above, not Stage 3 regressions.

## Player-experience round 2: minimal launch panel, per-challenge launcher visibility, and a real CSRF finding

More real playtest feedback landed after Stage 2's browser-verification
gap above was closed by the user actually trying it live (confirmed
working) — three further fixes, all shipped and live-verified:

- **Launch panel cut down to bare connect info.** The injected panel
  and `launch.html` both used to show a full explanatory paragraph on
  every view (a swarm-routing note, a shared-environment note, a
  pre-filled `ssh operator@...` command line with the wrong username for
  most levels). Stripped to just Host/Port (and, for Natas, the attacker
  links + target hostname), matching how OverTheWire's own site presents
  connection info — no prose, and the always-wrong `operator@` example
  is gone since there's no longer a pre-filled command at all.
- **New `InstanceChallengeConfig.show_launcher` flag** (default `True`).
  Real feedback: repeating a launch button on every level in a shared-box
  track (e.g. all 34 Bandit levels reusing one persistent box) is
  clutter once a player has launched it from any one of them. Only the
  three "Start Here" challenges per track now keep this `True`; every
  other level in a shared group sets it `False`. Exposed through
  `/api/status/<id>`, settable via both the admin form and the
  `/admin/mappings/sync` endpoint used by content sync scripts.
  `challenge-launch.js` now skips injecting its panel entirely when a
  challenge's status response says `show_launcher` is explicitly false —
  `/launch/<id>` and the JSON launch API are untouched, this only
  controls whether the frontend shows an unprompted control.
- **Real finding, not a CTFd permissions quirk:** the "403 on DELETE"
  pattern seen several times earlier in this project (challenge cleanup,
  hint cleanup) was finally root-caused while cleaning up duplicate hints
  during later content work — CTFd's global CSRF check
  (`CTFd/utils/initialization/__init__.py`) only checks the
  `CSRF-Token` header when `request.content_type` is exactly
  `application/json`; a bodyless DELETE with no `Content-Type` at all
  falls through to a form-nonce check that a header-only client never
  satisfies, and 403s. Adding `Content-Type: application/json` (even
  with an empty body) fixes it every time. Purely a client-side gap in
  how one-off admin scripts were built, not a CTFd bug or a real
  permissions gap — corrects the earlier record, which had written this
  off as minor test-hygiene noise.

Existing DB note still applies: `InstanceChallengeConfig` is
create-if-missing only (no Alembic migration), so an already-running
deployment needs a manual `ALTER TABLE ... ADD COLUMN show_launcher
BOOLEAN NOT NULL DEFAULT TRUE` when picking up this change — matches how
the table has been treated since it was introduced (see the docstring in
`__init__.py`).

## Critical finding: the orchestrator's in-memory state isn't actually shared (multi-worker race)

**Resolved** — the security audit's `fix/shared-vnc-password` branch
(merged to `main`) pinned `docker/orchestrator/Dockerfile` to
`--workers 1`, eliminating the multi-process race described below.
Confirmed still in place on current `main`. Left the original writeup
intact for the reasoning/repro steps.

Found live while verifying Stage 5's "Start Here" challenges in
`CEI-Labs-Wargames` (below) — this **supersedes and sharpens** the
already-documented "in-memory store, no reconciliation" limitation above.
That framing implied the desync only shows up after a restart or manual
Docker surgery. It doesn't: `docker/orchestrator/Dockerfile` runs
`gunicorn --workers 2`, meaning the orchestrator is **two separate OS
processes**, each with its own independent `InstanceStore`/`RangeStore`/
`PortAllocator` — no shared memory, no Redis, nothing. Every single
request has a real chance of landing on whichever worker didn't handle
the request before it.

**Reproduced directly, repeatedly, in normal steady-state operation (no
restart involved):**
- Launching two different challenges seconds apart produced a genuine
  port collision — `chinst-2-group-krypton` tried to bind the same port
  `chinst-2-group-bandit` had just been given, because the request landed
  on a worker that never saw the first allocation.
- Immediately after a *successful* launch, `GET /api/status/<id>` returned
  `status: null` for 3 of 4 consecutive polls in a row before finally
  hitting the worker that actually created the instance — a real player
  clicking Launch and watching Stage 2's injected panel would see it
  flicker back to "not started" even though their environment is already
  up.

This is worse than a rare edge case: with 2 workers, a naive request
distribution gives roughly 50% odds of any single request missing
whatever the other worker just did, compounding across every create/
status/reboot/relaunch/extend call. **Recommend treating this as
higher-priority than the other known-open items below** — it's the kind
of bug that looks like flakiness in testing but would be a constant,
confusing background hum in real multi-team play. Not fixed in this
pass (a real architectural decision — shared/Redis-backed state, or
dropping to `--workers 1` with something else providing concurrency —
deserves its own explicit call, not a drive-by fix buried in a content
stage). Workaround used to keep verifying past it: retry the same request
a few times.

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
- **`relaunch` can occasionally 500 with `network ... not found`.**
  `InstanceController.create_or_get(..., force_relaunch=True)` tears down
  the old service+network, then immediately recreates both in the same
  request — a real Docker Swarm eventual-consistency race (network removal
  isn't instant across the swarm's control plane; `ensure_network()` can
  create a same-named network moments before Swarm finishes tearing down
  the old one, and the following `services.create()` loses that race).
  Confirmed via live testing during the Stage 1 JSON-API pass below: not
  deterministic, an immediate retry of the same relaunch succeeds. Not
  fixed in this pass — pre-existing in `controller.py`, unrelated to the
  new JSON routes; worth a small delay/retry-on-`network not found` inside
  `create_or_get`'s relaunch path someday.

## Not done at all

Nothing — this is all merged to `main` now (see the status note at the
top). No PRs were opened; merged directly.
