# Session Handoff — 2026-07-13

Stopped mid-task at the user's request. This document is a complete
handoff so another agent (or the same one, later) can resume cleanly
without re-deriving context. Read this fully before doing anything else.

## Where things stand, in one paragraph

A live stress-test deployment of CEI Labs on `192.168.1.98` (SSH as
`ismaelrodriguez`, key-auth already set up) went through: (1) a real
multi-worker race-condition fix, (2) a five-persona adversarial testing
framework built into this repo, (3) round 1 of that testing (5 personas),
which found and fixed a relaunch/teardown race and found-but-could-not-fix
a network-airgap leak, and (4) round 2 (10 personas, 2 of each type)
which is **incomplete** — 6 of 10 subagents hit a session/API usage limit
mid-task, but the 4 that completed found that **the relaunch fix from
round 1 does not hold under round 2's real 10-persona concurrent load** —
a genuine regression, not a repeat of the original bug. That regression is
**not yet root-caused or fixed** — this is the most important open thread.

## Access and environment

- Deployment box: `192.168.1.98`, SSH as `ismaelrodriguez` (key auth
  already authorized from this machine — `~/.ssh/id_ed25519`).
- Repo on the box: `~/cei-labs-engine`, currently at commit `2fe010e`
  (one commit behind local `256c6e0`/whatever is current after this
  handoff's own commits — that gap is just a docs-only commit, no code
  change, not worth redeploying for by itself).
- Deployed orchestrator image: `cei-labs-orchestrator:race-fix`, built
  locally on the box (not pushed to GHCR) via
  `docker build -t cei-labs-orchestrator:race-fix -f docker/orchestrator/Dockerfile docker/orchestrator`
  then `docker service update --force --image cei-labs-orchestrator:race-fix --detach=true cei-labs_orchestrator`.
  **Always rebuild with this same tag after pulling new code** — Swarm
  won't redeploy on `--force` alone if the tag didn't change, but
  rebuilding first + `--force` always works.
- CTFd: `https://192.168.1.98/` with header `Host: ctfd.ctf.local`
  (self-signed cert, `curl -k` / `requests verify=False`). Two real test
  challenges exist: id 1 ("Test SSH 1: Readme"), id 2 ("Test SSH 2:
  Hidden File"), both `single-target` SSH boxes.
- CTFd admin credentials: `~/ctfd_admin_password.txt` and
  `~/ctfd_admin_token.txt` on the box (mode 600, both already rotated
  once this session after an accidental transcript leak — do not `cat`
  these files or any file that might contain them; read them
  programmatically into variables only, as the existing scripts on the
  box do).
- Orchestrator/plugin shared secret: `~/cei-labs-engine/docker/secrets/plugin_shared_secret.txt`
  on the box.
- Resource monitor: `glances` web server running on the box, bound to
  **loopback only** (`127.0.0.1:61208`) — deliberately not exposed to the
  LAN. To view: `ssh -N -L 61208:127.0.0.1:61208 ismaelrodriguez@192.168.1.98`
  from wherever you're working, then open `http://127.0.0.1:61208`.
  Started via `nohup ~/glances-venv/bin/python3 -m glances -w --port 61208 --bind 127.0.0.1 > ~/glances.log 2>&1 &` —
  check `ps aux | grep glances` before restarting it, it may still be running.
- `cei-labs-event` repo (separate, has `TRACKER.md`) received an external
  commit (`8ec3554`, not from this session) during this work — build on
  the current state of that file, don't assume it matches what this
  session last wrote.

## Full timeline this session (chronological)

1. Surveyed all 4 CEI Labs repos (`cei-labs-engine`, `cei-labs-net`,
   `CEI-Labs-Wargames`, `cei-labs-event`) for production readiness —
   see `cei-labs-event/TRACKER.md`.
2. Stood up a real Docker Swarm deployment of `cei-labs-engine` on
   `192.168.1.98` (after an aborted attempt to make the local Windows
   station the manager — Hyper-V/WSL2 networking couldn't receive
   inbound swarm traffic; abandoned in favor of the beefier remote Linux
   box as a single-node swarm).
3. Built and ran a direct orchestrator load-testing harness (staged
   3/10/20/40/burst-60 concurrency, plus a race-condition probe) —
   **reproduced a real multi-worker race condition live** (2x `201
   created` for the identical owner/key under `--workers 2`).
4. **Fixed the race condition**: rewrote `store.py`'s `InstanceStore`/
   `RangeStore` from an in-memory dict to a SQLite-backed store with
   atomic `reserve()`, so only one worker process ever calls the Docker
   API for a given instance. Commit `7656d3e`. Live-verified (0 duplicates
   under the same conditions that previously reproduced 2).
5. Built the adversarial persona testing framework: `docs/
   adversarial-persona-testing.md` (methodology, global rules of
   engagement, escalation plan) + 5 persona briefs under
   `docs/adversarial-persona-briefs/`. Commit `60f26d5`.
6. **Round 1**: 5 personas run concurrently. Found and fixed a real
   relaunch/teardown race (`c111fbd`, after a same-day follow-up fix to
   the fix itself — see that commit's message). Found a real network-
   airgap leak (real internet egress + real cross-instance reachability)
   — attempted a fix (`PublishMode=host` for published ports), **live-
   verified it silently doesn't bind on this deployment at all** (nothing
   in `ss -tlnp`, reproduced with a bare `docker service create --publish
   mode=host` against stock `nginx`, ruling out anything orchestrator-
   specific), reverted with the user's explicit sign-off (`2fe010e`).
   Full findings: `docs/adversarial-persona-findings-round-1.md`.
7. Along the way: leaked a CTFd admin API token into this session's own
   transcript via an incautious `cat`, rotated it properly (revoked the
   old one via a direct DB `DELETE` after CSRF-based API deletion kept
   403ing for reasons not fully diagnosed — see "Loose ends" below).
   Also surfaced and got explicit user sign-off that `cei-labs-engine`
   being a public repo with detailed unpatched-vulnerability writeups in
   its commit history is acceptable for now (pre-event test rig, no real
   participants).
8. Set up `glances` (see "Access" above) at the user's request for round 2.
9. **Round 2**: launched 10 personas (2 of each of the 5 types:
   `persona_alex_a/b`, `persona_sam_a/b`, `persona_jordan_a/b`,
   `persona_riley_a/b`, `persona_morgan_a/b`). **6 of 10 hit a session/API
   usage limit mid-task** (`persona_sam_a/b`, `persona_riley_a/b`,
   `persona_morgan_a/b` — the limit resets ~9:10pm America/New_York per
   the error message, though that's this session's limit, not
   necessarily still relevant by the time this is resumed). The 4 that
   completed (`persona_alex_a/b`, `persona_jordan_a/b`) all independently
   found the relaunch fix regresses under real concurrency — see next
   section. **Stopped here at the user's request before investigating
   the regression's root cause.**

## THE OPEN ISSUE: relaunch fix regresses under real concurrency

Round 1 verified the relaunch/teardown-race fix (`c111fbd`) at 8/8
success under light, sequential-ish load. Round 2's real 10-persona
concurrent load tells a different story:

- `persona_alex_a`: 5/16 relaunch attempts succeeded (~31%)
- `persona_alex_b`: 5/23 creation-triggering calls succeeded (~22%) —
  **and this hit cold first-time launches too, not just relaunches**
- `persona_jordan_a`: sequential rapid-fire (20x) now self-heals
  (19/20 fail-fast-then-recover, 1/20 succeeds outright), but a **true
  20-way parallel burst got 0/20 successes**, and a live SSH login
  confirmed an orphaned container survived ~7 minutes alongside a newer
  one (two distinct hostnames, two distinct flags, both answering SSH)
- `persona_jordan_b`: sequential success degraded with volume (20x=14/20,
  30x=4/30), **parallel bursts (25-way, 40-way) = 0% success**, plus a
  **new symptom**: one request in a 25-way burst returned a raw Flask
  HTML crash page instead of the plugin's normal JSON 500 — an unhandled
  exception escaping error handling under real parallel load

All four confirm: every failure is a plain `orchestrator returned 500:
internal error creating instance`, self-recovery is reliable on retry
(never permanently stuck across all 4 reports), reboot (a different code
path) stayed 100% reliable throughout, and no rate-limiting/backpressure
was ever observed on the launch endpoint (every failure is a 500, never a
429 — the orchestrator has no admission control, it just fails once
overloaded rather than queuing or rejecting cleanly).

**Working hypothesis, not yet confirmed**: `docker_client.py`'s
`remove_service()` (added in `c111fbd`) polls task-drain state with a
bounded `_TASK_DRAIN_TIMEOUT_SECONDS = 15.0` / `_TASK_DRAIN_POLL_INTERVAL
= 0.5`. Under real 10-persona concurrent Docker API load, actual
task-drain time is plausibly longer than 15s (more services being
created/removed simultaneously means more contention for the Docker
daemon's attention), so the bounded wait times out and "proceeds anyway"
(see the `logger.warning(...)` fallback in that function) — reintroducing
the same class of race the fix was meant to close, just less often than
before the fix existed. **This is a hypothesis, not confirmed** — the
investigation was stopped before checking the orchestrator's actual
logs/tracebacks for round 2's failures (unlike round 1, where checking
logs was what revealed the real root cause both times). That log-check is
the natural next step.

Also unexplained: why `persona_alex_a/b` saw failures on **cold** launches
(not just relaunches) — the reserve()/finalize() path from the original
race-condition fix (`7656d3e`) is a different code path than
teardown()/remove_service(), and shouldn't have the same drain-wait
dependency. Worth checking whether this is really a *new*, third
mechanism, or whether cold launches are colliding with a *different*
team's relaunch-in-progress somehow (e.g. via `PortAllocator` contention,
which is still a plain in-memory-per-process structure, never audited for
this exact race the way `InstanceStore`/`RangeStore` were).

## What to do next (in order)

1. Check `docker service logs cei-labs_orchestrator` from during round 2's
   test window for real tracebacks (the round 2 test ran roughly in the
   ~30-40 minutes before this handoff was written — adjust the `--since`
   window accordingly, or search for `ERROR app.main` / `Traceback`).
   This is the same technique that found the real root cause twice during
   round 1 — don't skip straight to guessing at a fix.
2. Once root-caused, fix it, add/extend a regression test in
   `docker/orchestrator/tests/` if the mechanism is reproducible in a
   fast unit test (the existing `test_store_concurrency.py` pattern —
   many independent Store objects racing via `threading.Barrier` — may
   need a similar test for whatever the actual mechanism turns out to
   be), then live-verify against the real deployment at a **real
   concurrency level matching round 2** (10+ concurrent relaunches/
   launches, not just round 1's lighter sequential-ish checks) before
   calling it fixed. Round 1's fix looked solid at low concurrency and
   wasn't — don't repeat that mistake by under-testing the next fix too.
3. Resume the 6 incomplete round-2 personas once the session limit
   allows. They were mid-brief, not started fresh — the cleanest way to
   resume is a **fresh launch** of each (not trying to resume the dead
   agent sessions, which are gone), using the same prompts as the
   original round-2 launches (see conversation history / the pattern in
   this session for `persona_sam`, `persona_riley`, `persona_morgan` —
   usernames `_a`/`_b` suffix, same brief files, same "don't re-litigate
   Finding 2" instructions, same hard-stop rules for personas 4/5).
   Consider running fewer at once (e.g. 3-4 in parallel instead of 6) to
   reduce the chance of hitting another usage limit mid-batch.
4. Once round 2 is fully complete (10/10) and any newly-found regression
   is fixed and re-verified, consolidate into
   `docs/adversarial-persona-findings-round-2.md` (matching round 1's
   format), update `cei-labs-event/TRACKER.md`'s relevant items and risk
   register, and reconsider whether Finding 2 (network airgap, still
   open from round 1) is worth another attempt with a different approach
   now that host-mode publishing is a confirmed dead end on this specific
   deployment (ideas noted in round 1's findings doc: isolate why host
   mode doesn't bind, or consider Traefik TCP routing as an alternative
   to direct port publish for challenges needing a strict airgap).

## Loose ends worth knowing about (not blocking, not investigated further)

- CTFd's `DELETE /api/v1/tokens/<id>` kept returning 403 despite an
  apparently-valid CSRF-Token header scraped fresh from an authenticated
  page — worked around by deleting the token row directly via SQL against
  the `ctfd-db` container instead. Never root-caused; if API-driven token
  management is needed again, expect to hit this and have the SQL
  workaround ready (`mysql -uctfd -p$(cat /run/secrets/ctfd_db_password)
  ctfd -e "DELETE FROM tokens WHERE id=<id>;"` via `docker exec` into the
  `cei-labs_ctfd-db` container).
- `docs/participant-quickstart.md`, referenced by round-1's Baseline
  Participant brief, doesn't exist in this repo (it's actually in the
  separate `CEI-Labs-Wargames` repo) — a cross-repo reference error in
  the brief itself, noted in round 1 findings as "Finding 5," not yet
  fixed in the brief file.
- The `connect_host` field for `single-target` challenges returns the
  bare `BASE_DOMAIN` (`ctf.local`), which doesn't resolve via DNS from a
  real client and has no `Host:`-header workaround the way CTFd's own
  `ctfd.ctf.local` does for HTTP — round 1's Finding 3, not yet fixed.

## Files/commits to know about

- `docker/orchestrator/app/store.py`, `controller.py` — the race-condition
  fix (commit `7656d3e`).
- `docker/orchestrator/app/docker_client.py` — the relaunch-race fix
  (`c111fbd`) and the reverted network-airgap fix attempt (`2fe010e`,
  read its commit message and the code comments left in place — they
  explain exactly what was tried and why it doesn't work here).
- `docker/orchestrator/tests/test_store_concurrency.py` — new regression
  test proving the original race-condition fix.
- `docs/adversarial-persona-testing.md`, `docs/adversarial-persona-briefs/*.md`
  — the testing framework itself.
- `docs/adversarial-persona-findings-round-1.md` — round 1's full writeup.
- `docs/adversarial-persona-round2-findings/*.md` — the 4 completed round
  2 personas' raw findings (not yet consolidated into a round-2 summary
  doc — that's step 4 above).
- `cei-labs-event/TRACKER.md` — cross-repo production-readiness tracker,
  updated after round 1, **not yet updated for round 2** (that's step 4
  above too).
