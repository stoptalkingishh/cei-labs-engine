# Round 2 Findings — Persona 3a: Impatient Power User (`persona_jordan_a`)

Target: `https://192.168.1.98/` (Host: `ctfd.ctf.local`), teams mode, account
`persona_jordan_a` / team `Team_Jordan_A` (user id 21, team id 15). Concurrent
with sibling `persona_jordan_b` and 8 other personas hammering the same
deployment. Session: 2026-07-13, ~18:38–18:50 local time.

## Methodology note (not a platform finding): scratchpad filename collision

Early in the run, generic filenames (`cookies.txt`, `reg_headers.txt`,
`challenges.html`) in the shared scratchpad directory got clobbered mid-run —
`cookies.txt` ended up holding a session for `persona_jordan_b` (and briefly
what looked like `persona_sam_b`) instead of my own account, because my
sibling agent instance was writing to the same generic filenames in the same
directory concurrently. Confirmed via `/api/v1/users/me` returning the wrong
identity, then re-confirmed clean by logging in fresh into an isolated
subdirectory (`jordan_a_private/`) with uniquely-prefixed filenames
(`ja_*`), which correctly returned `persona_jordan_a` (id 21, team 15). All
testing below was done from that isolated directory. **This was my own
tooling artifact, not a CTFd session-fixation bug** — flagging only so
whoever reads this doesn't mistake the early garbled output for a real
finding.

## Finding A (real bug, reproducible): true concurrent relaunch burst against
an active instance still produces a real, persistent orphaned container —
fix from round 1 does not fully hold under heavier concurrency

Round 1's Finding 1 fix (bounded task-drain polling in `remove_service()`)
holds for *sequential* rapid-fire relaunch (see Finding B) — it always
self-healed to exactly one live instance with no orphan across that test.
It does **not** hold under a truly concurrent burst:

**Request pattern:** 20 `relaunch` requests fired **in parallel** (backgrounded
curl processes, all launched in the same shell loop with `&` + `wait`, not
sequential) against challenge 1's already-active instance (port 32021 →
originally port 32000 pre-burst).

**Outcome:** 0/20 succeeded — every single request returned
`{"error":"orchestrator returned 500: internal error creating instance","success":false}`.
Immediately after, `GET .../status/1` returned `"status":null` — CTFd's own
state believes **no instance exists** for this challenge/team (a "vanished"
instance from the participant's perspective). But the pre-burst container on
port 32000 was **still live and fully functional**: confirmed via real SSH
login (`player`/`player`, credentials from the challenge description) —
hostname `53c874e3cf3f`, flag `tn2O5Vn9Wrw3LBTTWsq-0oqi`.

A subsequent single `relaunch` call recovered cleanly, producing a new
instance on port 32021 (hostname `9b2a255e4cc3`, flag
`gwoWymUJDIebgoOGvIXLZPFm` — different flag confirms a genuinely different
container, not the same one re-reported). **Both containers were then
simultaneously live and independently reachable** — confirmed by SSH'ing
into both at once and getting two different hostnames/flags/IPs. This is a
real duplicate-instance state: CTFd only knows about one, but two real
containers exist and both answer SSH with a working challenge environment.

**The orphan never self-healed.** Re-checked at T+3min05s, T+6min25s, and
T+~7min: port 32000 answered every time with the same hostname
(`53c874e3cf3f`). No reaper sweep reclaimed it during the ~7-minute test
window that followed.

**Reproducibility:** re-ran the identical pattern (15 parallel relaunches)
against challenge 2's active instance. Same immediate outcome — 0/15
succeeded, status went to `null`. But this time the old port did **not**
remain reachable after recovery — properly cleaned up. So the orphan is
**racy, not deterministic**: it happened on the first attempt with concrete,
irrefutable evidence (two live SSH sessions, two different flags) but not on
the second attempt under an outwardly identical request pattern. This is
consistent with round 1's Finding 2 hypothesis (stale un-drained container
left on a reused network/port) but reproduced here via a much heavier,
fully-parallel burst than round 1's — round 1's own 15-request burst that
found the original orphan was likely less perfectly simultaneous than
`&`+`wait`-backgrounded curl.

**Recovery cost when it does happen:** challenge 2's post-burst recovery took
8 total consecutive failed retries (spread across ~20s of 1-2s-spaced
retries) before a `relaunch` finally succeeded — no permanent lockout, but a
meaningfully long, undifferentiated string of generic 500s with zero
distinguishing signal (rate-limit message, retry-after header, queue
position) telling the participant "keep trying, this is transient."

## Finding B: sequential tight-loop relaunch — self-heals cleanly, but most
requests fail fast (not going through the 15s drain wait)

**Request pattern:** 20 `relaunch` requests fired sequentially, no delay
between them (tight `for` loop, prior request's response received before
next is sent) against challenge 1's active instance (port 32010 pre-loop).

**Outcome:** 19/20 failed with the same `orchestrator returned 500` error;
each failing response returned in **40–200ms** — far faster than the 15s
task-drain poll bound the round-1 fix introduced, meaning these are failing
fast on a collision/lock rather than actually waiting out a drain. The 20th
request succeeded, landing on a new port (32000). Post-loop: old port
(32010) correctly closed, new port (32000) live and correct — **end state
was exactly one working instance, matching what a single legitimate action
should produce.** No orphan resulted from this specific run (the orphan in
Finding A came from the fully-parallel burst, not this sequential one).

**Takeaway:** relaunch-of-active is still not safe to call repeatedly in
quick succession — it's not idempotent/lock-protected, so N rapid calls
produce up to N-1 wasted 500s before one wins — but at least in the
sequential case tested, it did not leak a container. The parallel case
(Finding A) did.

## Finding C: first-ever launch of a completely fresh instance also
transiently 500'd

Before any relaunch testing, the very first `default`-action launch of
challenge 1 (never launched before, `status:null`) failed once with the
identical `orchestrator returned 500: internal error creating instance`,
then succeeded on immediate retry. Round 1's Finding 1 was scoped
specifically to relaunch-of-*active*; this suggests the same underlying
contention can surface on any concurrent instance-creation path, not just
teardown-then-recreate — plausibly explained by 10 personas' agents all
starting up around the same time and contending for the orchestrator/port
allocation. Only observed once (not systematically re-tested against a
guaranteed-fresh challenge, since only 2 challenges exist and both got used
immediately).

## Finding D (expected-correct, reboot remains fully robust): 15x rapid
reboot loop, no delay — 15/15 succeeded

Reboot ("Reboot Host", restart-in-place) was fired 15 times in a tight
sequential loop against the active instance with zero pause. Every single
call succeeded, same port throughout (32021) the entire time. This matches
round 1's finding that reboot (restart-in-place) is reliable — the failure
mode is specific to relaunch's teardown+recreate path, confirmed again here
under an even tighter loop than round 1 used.

## Finding E (not a bug, coverage gap): extend-cap scenario could not be
exercised on this deployment

Solved challenge 1 for real (submitted the legitimate flag retrieved from my
own instance, `gwoWymUJDIebgoOGvIXLZPFm` — correct, 100 points awarded).
Checked `status` immediately and repeatedly afterward (including a 5s wait
and later, multi-minute-later checks): `shutdown_at` never appeared, and
`extend` consistently returned
`{"error":"no shutdown is currently scheduled for this instance","success":false}`
across 15 rapid repeated calls (well-formed, consistent, no crash — that
part is correct behavior).

Root cause (confirmed by reading `docker/ctfd/plugins/instance-launcher/solve_hook.py`
locally, not by hacking the live DB): the post-solve shutdown countdown only
fires if the challenge's `InstanceChallengeConfig.shutdown_on_solve` is
`True`. Neither of this deployment's two test challenges appears to have
that flag set (or has no `InstanceChallengeConfig` row at all) — solving
never triggers `schedule_shutdown`, by design, silently and without error
(the hook is deliberately best-effort/fail-silent). **This means the
extend-cap scenario in my brief (`SHUTDOWN_MAX_EXTENSIONS`, default 3 per
`docker/orchestrator/app/config.py`) could not be exercised live on this
deployment at all** — not a bug, just a config gap for this specific round
that's worth calling out since the brief explicitly asked for it and it's
untestable as currently configured.

## Capacity ceiling: not independently reachable by one honest account

Only two challenges exist, so one account launching only its own instances
caps out at 2 concurrent instances — no artificial per-account rate limit or
capacity error was ever encountered (every failure was the generic
orchestrator 500 above, never a distinct 429/capacity message). Source
inspection (`docker/orchestrator/app/config.py`) shows a global
`ORCHESTRATOR_MAX_INSTANCES` default of 30, shared across all accounts —
noted for context only, not independently verified live (out of scope to
try to exhaust the shared pool per my brief's left limit).

## Finding 2 (already open, not investigated further per instructions)

Every container SSH'd into during this round (orphan and legit alike) showed
3 network interfaces (overlay + two others), consistent with round 1's
already-documented root cause for the network egress/cross-instance-reach
finding. Not re-investigated — noted only as requested.

## Summary of exact counts

| Test | Pattern | Success | Fail | Final state |
|---|---|---|---|---|
| Fresh launch, ch1 | 1 sequential | 1/2 (1 retry) | 1/2 | clean |
| Relaunch tight loop, ch1 | 20 sequential, no delay | 1/20 | 19/20 | clean, no orphan |
| Relaunch true burst, ch1 | 20 parallel | 0/20 | 20/20 | **status vanished (null); recovery relaunch succeeded but left a confirmed live duplicate/orphan container reachable for 7+ min** |
| Relaunch true burst, ch2 | 15 parallel | 0/15 | 15/15 | status vanished (null); recovered after 8 more sequential retries; **no orphan this time** |
| Reboot tight loop, ch1 | 15 sequential, no delay | 15/15 | 0/15 | clean, same port throughout |
| Extend, ch1 (post-solve) | 15 sequential | 0/15 | 15/15 (all "no shutdown scheduled") | expected — shutdown_on_solve not configured for this challenge |

**Bottom line for the round-1 re-test question:** the relaunch/orphan fix
holds for sequential rapid-fire (Finding B) but **does not fully hold under
true parallel concurrency** — a 20-request simultaneous burst against one's
own active instance can still leave a real, independently-reachable,
fully-functional orphaned container (distinct hostname, distinct flag,
confirmed via live SSH) that persisted for the entire remainder of this
~7-minute test session with no sign of reaper cleanup. This is a new,
heavier-concurrency manifestation of round 1's Finding 1/2 pattern, not a
brand new root cause — but it shows the fix's coverage boundary: sequential
abuse is now safe, truly parallel abuse is not.
