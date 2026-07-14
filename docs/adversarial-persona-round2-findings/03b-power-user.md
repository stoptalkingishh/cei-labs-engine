# Round 2 Findings: Persona 3B, Impatient Power User (`persona_jordan_b`)

Target: `https://192.168.1.98/` (Host: `ctfd.ctf.local`), teams mode, account
`persona_jordan_b` / team "Team Jordan B", concurrent with 9 other personas
(including sibling `persona_jordan_a`) per the round 2 escalation plan.

All requests authenticated as my own account against my own two instances
(challenge 1 "Test SSH 1: Readme", challenge 2 "Test SSH 2: Hidden File").
No admin endpoints probed, no other team's IDs touched. ~200 lifecycle
requests total across all scenarios below, plus 8 flag-submission requests.

## Headline: Round 1's relaunch fix does NOT hold under round 2's heavier concurrency

Round 1 verified 8/8 back-to-back relaunches succeeded. Under round 2's real
10-persona concurrent load, the same exact request pattern regressed hard:

| Test | Pattern | Result |
|---|---|---|
| Relaunch loop 1 (ch1) | 20x sequential `relaunch`, no delay | 14/20 success, 6/20 `500` |
| Relaunch loop 2 (ch1) | 30x sequential `relaunch`, no delay | **4/30 success, 26/30 `500`** |
| Relaunch loop (ch2) | 25x sequential `relaunch`, no delay | 2/25 success, 23/25 `500` |
| Concurrent burst (ch1) | 25x truly parallel `relaunch` (backgrounded, `wait`) | **0/25 success**; 24 JSON 500s + 1 raw Flask HTML 500 crash page (see below) |
| Concurrent burst (ch1+ch2 mixed) | 40x truly parallel `relaunch`, 20 per challenge, fired together | **0/40 success on either challenge** |

Every failure returned the same orchestrator error body as round 1:
`{"error":"orchestrator returned 500: internal error creating instance","success":false}`
at HTTP 200 (the plugin's own JSON wrapper, not a raw 500 status).

**One anomalous response differed from all the others**: request #2 of the
25-way parallel burst against challenge 1 returned a full CTFd Flask HTML
error page (`<h2>An Internal Server Error has occurred</h2>`, title "CEI
Labs Cyber Range (load-test)") instead of the plugin's normal JSON error
body — meaning under true parallel load an unhandled exception can escape
the plugin's own error handling and hit CTFd's generic error page. Saved as
`burst_2.txt` in this scratch dir if it needs inspecting.

### Zero-instance state reproduced twice (the exact "never zero" violation the brief calls out)

- After relaunch loop 2 (30x sequential on ch1), `GET status/1` returned
  `"status": null` — CTFd had torn down the old instance and never
  successfully recreated it. Required one explicit extra `default` call
  (~12s later) to recover; two further immediate calls both also
  succeeded once state stabilized.
- After the 40x mixed concurrent burst (20 parallel each on ch1+ch2 at
  once), **both** of my instances went to `"status": null` simultaneously
  — not just one. Recovery required a separate explicit `default` call per
  challenge; ch1 recovered on the first retry, ch2 needed one 500 before
  succeeding on the second retry ~2s later.

So: self-service recovery is always possible with one more manual action,
matching round 1's finding that reboot/re-launch never permanently bricks
an instance — but the "always end up with exactly one working instance,
never zero" bar from the brief was violated twice, and got *worse*, not
better, as concurrency increased (100% relaunch success in round 1's light
load -> 13-16% relaunch success under round 2's 20-40-way real concurrency).

### Reboot (restart-in-place) remains fully reliable — unaffected

15/15 rapid-fire `reboot` calls against challenge 1 succeeded, 0 failures,
same port returned every time. Confirms round 1's finding that reboot is a
structurally different, unaffected code path from relaunch (teardown +
recreate) — the regression above is specific to relaunch/first-launch
(both hit the orchestrator's instance-creation path), not general
lifecycle-endpoint instability.

### First-launch (not just relaunch-of-active) also hit the bug

Launching challenge 2 for the very first time (no prior instance, plain
`{"action":"default"}`) 500'd three times in a row (22:40:46 / :48 / :50),
then succeeded on a 4th attempt after an 8s pause. This is notable because
round 1's Finding 1 was specifically about relaunch-of-*active*-instance;
here the identical 500 hit a cold-start create-from-nothing call, meaning
the regression isn't scoped to teardown-then-recreate — it's whatever
shared orchestrator resource (Swarm API, network-name allocation, port
pool) gets contended under concurrent create calls from multiple personas/
challenges at once.

## Real orphaned/reachable container confirmed (Finding 1/2-adjacent, new evidence)

During relaunch loop 1 on challenge 1, one successful relaunch response
handed back `connect_port: 32001`. My status later moved on to other ports
(32007 -> 32013 -> 32014 -> 32017 after subsequent relaunches/recovery).
I retested raw TCP connectivity to every port my own account had ever been
handed, at multiple points over an ~8-minute window:

```
port 32001: OPEN — live SSH banner "SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u10"
            (grabbed via raw /dev/tcp banner read, no auth attempted)
port 32005: closed (superseded, correctly torn down)
port 32006: closed (superseded, correctly torn down)
port 32007: closed (superseded, correctly torn down)
port 32013: closed (superseded, correctly torn down)
port 32014: closed (superseded, correctly torn down)
port 32027: closed (superseded, correctly torn down)
```

Port 32001 is the **only** one of seven superseded ports that stayed open
across the whole test, re-verified open on a final recheck ~8 minutes after
it was superseded and my status had moved to a completely different port
(32017). CTFd's own status API no longer attributes any live instance to
32001 for my account. This reproduces exactly the symptom class in round
1's Finding 1/2 (a torn-down instance's port not actually freed at the
network level) even though 6/7 other superseded ports in this same test run
*did* clean up correctly — consistent with a residual race rather than a
total regression of the round-1 fix. I did not attempt to identify whose
container this is or authenticate to it (out of scope, matches round 1's
Account & Answer Cheater's same restraint on its `.2`-host finding);
flagging reachability only, per the brief.

A broader port-range scan (32000-32020) turned up several more OPEN ports
(32000, 32002, 32011, 32015, 32018, 32019, 32020) — I'm **not** claiming
these as orphans; with 10 concurrent personas x up to 2 challenges each,
most of these are plausibly other teams' legitimate active instances, and I
have no way to distinguish that from this account. Only 32001 has the
specific evidence (explicitly handed to my own account, explicitly
superseded by my own subsequent successful relaunch, still live well after)
that makes it a solid finding rather than scan noise.

## Extend / shutdown-countdown cap: not reachable in this test window

`extend` was fired 25x rapid-fire against challenge 1 before any solve —
correctly and consistently returned
`{"error":"no shutdown is currently scheduled for this instance","success":false}`
every single time, no crash, no silent no-op.

Solved challenge 1 for real (SSH'd in as `player`/`player` via plink,
`cat ~/readme`, flag `LVPsDUBSH86udZL353fod91F`, submitted via
`/api/v1/challenges/attempt`, confirmed `"status":"correct"`) specifically
to trigger the post-solve shutdown countdown described in
`solve_hook.py`. No countdown appeared afterward — `extend` continued to
report "no shutdown is currently scheduled" and `status/1`'s `idle_seconds`
kept counting normally with no shutdown field. Per `solve_hook.py`'s own
logic this is gated on `InstanceChallengeConfig.shutdown_on_solve`, which
this deployment's test challenges appear to have disabled (or unreachable
without DB/admin access to confirm) — **not** flagging this as a bug, just
noting the extension-cap scenario in the brief could not be exercised on
this deployment's current challenge config. `extend`'s own no-op path is
solid and well-behaved regardless.

## Capacity ceiling: structurally limited by the 2-challenge deployment

Brief asks to keep launching different challenge instances until hitting a
capacity/rate limit. Only two real challenges exist (id 1, id 2) on this
deployment, so my own account's ceiling is trivially "2 concurrent
instances" by content availability, not by any enforced platform limit —
both launched and stayed launched simultaneously throughout, no 429 or
capacity-error ever seen on the instance-launcher endpoints across ~200
requests. Notably: **no rate-limiting/backpressure of any kind was ever
observed on `/plugins/instance-launcher/api/launch/*`** at any burst size
(20, 25, 30, 40 concurrent/rapid) — every failure was a `500`, never a
`429`. That's arguably a gap on its own: nothing stops an impatient-but-
honest client from hammering the orchestrator hard enough to reliably
produce zero-instance states, as demonstrated above.

## What held correctly under this heavier load

- **Reboot**: 15/15, no failures, no port churn.
- **Flag-submission rate limiting**: burst of 8 wrong submissions to
  challenge 2 got exactly 7x `200 incorrect` then `429 Too Many Requests`
  on the 8th — consistent with round 1's "burst-of-5ish then 429", still a
  real, working throttle under round 2 load.
- **Unknown `action` value**: `{"action":"bogus_action_xyz"}` silently
  no-op'd to the default/status-return behavior (`success:true`, existing
  instance returned unchanged) — same minor/cosmetic gap round 1 already
  noted, still present, still non-crashing.
- **Registration / teams-mode / CSRF**: clean throughout, no issues.
- **Self-service recovery**: every zero-instance state I hit was
  recoverable with one more explicit call — never required any operator
  intervention.

## End state

Both my instances left running and healthy: challenge 1 on port 32017,
challenge 2 on port 32029. Challenge 1 solved (flag submitted, confirmed
correct). No cross-team access attempted, no admin endpoints touched, no
irreversible action taken.

## Summary verdict

**Round 1's Finding 1 fix (relaunch-of-active) does not hold under round
2's real 10-persona concurrency** — it degrades from 100% success (round
1's light/sequential load) to as low as 0% success under true parallel
bursts, and reproduces the exact "ended up with zero working instances"
failure mode round 1's brief explicitly says must never happen, on two
separate occasions (once single-challenge, once across both of my
challenges simultaneously). This looks like a real regression/incomplete
fix under concurrency, not a new bug — same root-cause family as round 1's
Finding 1 (shared Swarm resource contention during teardown+recreate),
just not fully closed by the round 1 patch once enough concurrent load hits
the same resource. Additionally reproduced one persistent orphaned/live
container on a superseded port (Finding 1/2-adjacent, new corroborating
evidence, not a new root cause) and one raw Flask HTML crash page escaping
the plugin's normal JSON error handling under true parallel load. Finding 2
(egress/cross-network) was not separately investigated per instructions;
the port-1/2-network-leak style symptom (a stale port still reachable) is
consistent with what round 1 already logged as open.
