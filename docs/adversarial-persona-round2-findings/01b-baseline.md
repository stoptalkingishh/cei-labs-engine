# Round 2 Findings: Baseline Participant, instance B (`persona_alex_b`)

Target: `https://192.168.1.98/` (Host: `ctfd.ctf.local`), teams mode.
Account: `persona_alex_b`, team `Team_Alex_B` (team id 12, user id 18).
Run: 2026-07-13, concurrent with 9 other personas (2x each of 5 types) per
round 2's escalation plan.

## Headline: Finding 1's relaunch fix does NOT hold under round-2 concurrency

Round 1 verified 8/8 back-to-back relaunches succeeding against a single
active instance with no other personas running. Under round 2's 10-concurrent
load, relaunch/launch (instance-creation) calls failed at a high rate — the
exact same error string as round 1's original bug
(`orchestrator returned 500: internal error creating instance`) — but,
unlike round 1's *100%* failure, this round's calls eventually succeeded on
retry every single time (never permanently stuck). Aggregate tally across
both challenges' launch/relaunch/plain-launch actions this session:

| Action sequence | Attempts | Success | Fail |
|---|---|---|---|
| Challenge 1 initial launch | 1 | 1 | 0 |
| Challenge 1 reboot | 1 | 1 | 0 |
| Challenge 1 relaunch/launch (all subsequent) | 17 | 3 | 14 |
| Challenge 2 initial launch | 4 | 1 | 3 |
| Challenge 2 relaunch | 2 | 1 | 1 |
| **Total (creation-triggering only)** | **23** | **5 (22%)** | **18 (78%)** |

Every failure returned identical JSON:
`{"error":"orchestrator returned 500: internal error creating instance","success":false}`
with HTTP 200 (the error is inside the JSON body, not the status code).

Notable detail: this reproduced on a **first-ever launch** of challenge 2
(never previously relaunched, no prior teardown race possible for that
service name) — 3 straight 500s before the first successful create. This
means round 2's failure mode is broader than round 1's Finding 1: round 1
was specifically "relaunch of an *already-running* instance races its own
teardown"; this round shows the *same* orchestrator error on cold creates
under concurrent load from 10 personas, which points at contention/exhaustion
somewhere in the shared create path (port allocation, Swarm API queueing, or
the same task-drain-polling loop from the Finding 1 fix now serializing and
timing out under concurrent callers) rather than a full regression of the
Finding 1 fix itself. Recommend the operator check orchestrator logs from
this window for the underlying exception, not just the generic 500 CTFd
displays.

**Verdict: real bug (regression/gap), not the same as round 1's Finding 1,
but same symptom string.** Self-service recovery *does* work (persistent
retry, ~15-45s apart, always eventually succeeded), so this is degraded
reliability under load rather than a hard break — but a real participant
retry-mashing the "Relaunch Environment" button 5-10 times inside a minute,
exactly as the UI's own countdown/poll pattern invites, would plausibly see
repeated failures during any high-concurrency window (event day with many
teams launching/relaunching around the same time, e.g. right after kickoff).

**Idempotency check (good)**: once an instance is up, re-issuing plain
`action:null` "launch" while already running just returns the existing
status with `success:true` and no side effects (verified: two consecutive
calls both returned identical port 32031, no double-create, no new
container). This part is correct.

## Finding-2-adjacent symptom observed (not investigated further, per instructions)

After a failed relaunch attempt left challenge 1's tracked status as `status:
null` (CTFd/orchestrator's own view: no instance exists), the **previous**
instance's port (32000) still answered real SSH traffic for at least several
minutes afterward — a stale/orphaned container CTFd no longer tracks but
that is still live and reachable. Consistent with round 1's already-open
Finding 2 (incomplete network/service cleanup interacting with the shared
ingress path); not re-investigated per this round's scope instructions.

## Full happy-path journey (steps 1-7 of the Baseline Participant brief)

1. **Register**: `POST /register` with a scraped `csrfNonce` worked first
   try, 302 to `/team` (teams-mode gate). No issues.
2. **Create team**: `POST /teams/new` (name `Team_Alex_B`, password) worked
   first try, 302 to `/challenges`. Login/session cookie correctly carried
   through.
3. **Challenge list**: `GET /api/v1/challenges` returned both challenges
   (`id 1`: Test SSH 1: Readme, `id 2`: Test SSH 2: Hidden File), both
   `single-target`. `/rules` still returns 404 (matches round 1's minor
   finding — not re-investigated, just confirmed still present).
4. **Launch challenge 1**: succeeded first try.
   `connect_host` returned as bare `ctf.local` — confirms round 1's Finding 3
   is still open: this hostname does not resolve (no entry in this box's
   `C:\Windows\System32\drivers\etc\hosts`, no DNS). Worked around by
   connecting directly to the orchestrator/swarm node IP
   (`192.168.1.98:<connect_port>`) instead, which a real participant would
   have no documented way to discover — this is exactly the gap Finding 3
   already describes, now re-confirmed on a second, independent account.
5. **Connect + solve challenge 1**: SSH (`player`/`player`) to
   `192.168.1.98:32000` worked; `readme` file contained the flag
   (`D3VLp5EljWtpJ0n_UexGUQ7E`); submitted via
   `POST /api/v1/challenges/attempt`, got `{"status":"correct"}`.
6. **Launch + solve challenge 2**: first-launch attempts failed 3x with the
   headline 500 above, succeeded on the 4th attempt (~20s later, port
   32004). Flag was hidden at `.hidden/deep/.flag`
   (`gWm4S7bBPGbsxDK5BD7YDDJX` on first launch — noted a **different** flag
   value on a later relaunch of the same challenge, `fY0bRfJcoEQtnnVN7T9OUX5I`
   — consistent with a per-launch dynamic/rotating flag design, not a bug,
   just worth flagging as a documented-or-not behavior: a participant who
   relaunches *after* already reading (but not yet submitting) their flag
   would find it silently invalidated). Submitted successfully.
7. **Reboot**: `action:"reboot"` on the running challenge-1 instance
   succeeded first try, same port retained (32000), environment and flag
   file intact afterward — reboot (restart-in-place) is reliable, matching
   round 1's finding that this action was never the broken one.
8. **Relaunch (Finding 1 re-verification)**: see headline section above —
   this is where the round's real finding lives.
9. **Logout/login persistence**: `GET /logout` (302), then fresh login as
   `persona_alex_b` — `solved_by_me: true` correctly persisted for both
   challenges post-relogin. Challenge 2's still-running instance (port
   32024 at that point) persisted correctly across the session boundary.
   Challenge 1's instance did **not** persist across logout/login only
   because it happened to be in one of the "orchestrator 500" failure
   windows at that exact moment (state is tied to the *instance*, not the
   session — re-attempting launch after relogin eventually succeeded, port
   32031) — this is a symptom of the headline bug, not a separate
   logout/login bug.

## What held correctly

- CSRF token flow (scrape nonce from page, `CSRF-Token` header) required and
  respected on every state-changing call — no way found to skip it.
- Team-mode registration gate (`/register` → forced `/team` until a team is
  created) enforced correctly.
- Progress (solves) and running-instance state both survive logout/login.
- Flag submission API behaves exactly as documented (`correct` /
  presumably `already_solved` / `incorrect` — only tested `correct` this
  round since each flag was submitted once).
- Idempotent "launch when already running" returns current state, no
  duplicate containers.
- Reboot (restart-in-place) 100% reliable across all attempts this session.

## Wall-clock

Full journey including the extended relaunch reliability probing (which
was explicitly requested this round, beyond the base brief's single
reset/reboot check): roughly 25-30 minutes real time, dominated by the
15-45s waits between relaunch retries while chasing the headline finding's
failure rate. The core happy path alone (register → team → launch →
connect → solve, both challenges, once each) would be well under 10 minutes
had relaunch/launch not needed repeated retries.
