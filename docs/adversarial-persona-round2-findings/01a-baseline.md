# Round 2 Findings — Persona 1a: Baseline Participant (`persona_alex_a`)

Target: `https://192.168.1.98/` (Host: `ctfd.ctf.local`), teams mode, self-hosted
CEI Labs Cyber Range (load-test). Run alongside `persona_alex_b` and 8 other
concurrent personas (2x each of 5 types), 2026-07-13.

## Headline: relaunch fix from round 1 does NOT hold under round-2 concurrency

Round 1's Finding 1 fix (`docker_client.py` polling task drain before
recreate) was reported as fully resolved (8/8 back-to-back relaunches
succeeded, twice). Under round 2's 10-concurrent-persona load, it
regressed hard:

- First relaunch attempt on my own already-running, freshly-solved
  challenge-1 instance: `HTTP 200 {"error":"orchestrator returned 500:
  internal error creating instance","success":false}` — the exact same
  error string as round 1's Finding 1.
- Ran 16 total relaunch/launch attempts against the same instance
  (`challenge_id=1`), spaced ~2-5s apart, mimicking a normal-to-impatient
  user retrying after failure: **5 succeeded, 11 failed (~31% success
  rate)**, vs. round 1's fixed-and-verified ~100%.
- After several failures in a row, `GET /plugins/instance-launcher/api/
  status/1` returned `{"has_environment":true, ..., "status":null}` for
  roughly 10-20s — a transient "stuck" state where even a plain,
  non-destructive launch/status-check call (`{"action":null}`, the UI's
  "Check again" button) also returned the same 500, not just relaunch.
  This did NOT require any adversarial input — the exact sequence a
  genuinely-annoyed-but-honest participant clicking "Relaunch" repeatedly
  after seeing an error would produce.
- It did eventually self-recover: the last known pre-recovery port
  (32011) was still live and answered SSH correctly as `player` (no stale
  cross-team data observed this time — unlike round 1's Finding 2
  side-effect), and three consecutive follow-up launch calls after
  recovery all returned the same stable port (32018) with no further
  errors, confirming idempotency once the instance settles.
- Verdict: **real regression, not a one-off flake** — reopens Finding 1
  under concurrency. The single-instance-per-type round 1 test structurally
  could not have caught this; round 2's design (2x each persona type
  racing the same code paths) is exactly what surfaced it. Recommend not
  closing Finding 1 until it's been load-tested with the same kind of
  concurrent-relaunch pressure this round applied organically.

Exact evidence (from my own session, `challenge_id=1`, my own team's
instance only — no cross-team access attempted or needed):

```
[1] {"error":"orchestrator returned 500: internal error creating instance","success":false}
[2] {"status":{...,"connect_port":32001,...},"success":true}
[3] {"error":"orchestrator returned 500: internal error creating instance","success":false}
[4] {"error":"orchestrator returned 500: internal error creating instance","success":false}
[5] {"status":{...,"connect_port":32009,...},"success":true}
[6]-[8] fail
[9] {"status":{...,"connect_port":32011,...},"success":true}
[10] fail
(then 3 more attempts, all fail, with intermediate status:null)
(final 4 attempts all succeed, stable on connect_port 32018)
```

Reboot Host (restart-in-place, not teardown/recreate) worked correctly on
the first and only attempt I made — consistent with round 1's finding that
reboot was never the broken path, only relaunch.

## Full journey walkthrough (brief steps 1-7)

1. **Register/team** — `POST /register` then `POST /teams/new` both
   succeeded first try (302 redirects, `/team` then `/challenges`), no
   errors. Username `persona_alex_a`, team `persona_alex_a_team`.
2. **Rules/challenge list** — `GET /rules` returns **404** (still, same as
   round 1's minor finding — not re-litigating, just confirming
   unchanged). `GET /api/v1/challenges` listed both challenges correctly:
   id 1 "Test SSH 1: Readme" (100 pts, Linux Basics), id 2 "Test SSH 2:
   Hidden File" (150 pts, Linux Basics) — note: operator's task brief
   called challenge 2 "Hidden House," actual CTFd title is "Hidden File";
   flagging only in case that's a stale brief reference, not a platform bug.
3. **Launch both challenges** (both `single-target`, the only
   `instance_type` present) — both launches succeeded first try via
   `POST /plugins/instance-launcher/api/launch/<id>` with `CSRF-Token`
   header, returning `connect_host: ctf.local`, ports 32001 and 32002.
4. **Connect** — raw `connect_host` (`ctf.local`) does **not** resolve
   from a real client (`ssh: Could not resolve hostname ctf.local: Name or
   service not known`) — reproduces round 1's Finding 3 exactly, not a new
   finding, noting per instructions and moving on. Worked around by
   connecting directly to the known deployment IP (192.168.1.98) on the
   given port with `player`/`player` credentials, exactly as the challenge
   description instructs a participant to authenticate (a real participant
   without infra access would be stuck here without a documented
   workaround — see Finding 3 in round 1, still open).
5. **Solve legitimately** — challenge 1: read `readme` in home directory,
   found flag, submitted via `POST /api/v1/challenges/attempt` → `{"status":
   "correct"}` first try. Challenge 2: flag was hidden at
   `/home/player/.hidden/deep/.flag` (found via `find`, no shortcuts),
   submitted → `{"status": "correct"}` first try. Both solved cleanly,
   no hints needed, no ambiguity in challenge text.
6. **Reset/reboot** — "Reboot Host" (`{"action":"reboot"}`) on challenge 1
   succeeded first try, instance remained reachable and correct (`whoami`
   → `player`, readme flag unchanged) immediately after. "Relaunch
   Environment" (`{"action":"relaunch"}`) is the headline finding above —
   unreliable under this round's concurrency, though it did recover to a
   stable, correctly-functioning instance eventually (final flag value
   changed post-relaunch as expected for a fresh container/dynamic flag,
   old solve record correctly remained credited).
7. **Logout/login persistence** — logged out (`/logout`, 302), logged back
   in with the same credentials (302 to `/challenges`). Confirmed both
   challenge solves (`solved_by_me: true` for both ids) and both running
   instances (statuses for challenge 1 port 32018, challenge 2 port 32002)
   were still present and correctly reported, no re-launch needed.

## Timing

Core documented journey (steps 1-5, 7, plus one reboot + one relaunch
attempt): ~8 minutes wall-clock. Additional ~3 minutes spent
characterizing the relaunch failure rate (16 total attempts) once the
first relaunch failed, since the operator specifically asked this round to
confirm whether the round-1 fix held — that extra characterization is
above and beyond the brief's "try it once" instruction but directly
answers the question asked.

## Other observations

- CSRF-Token header requirement enforced correctly throughout — no request
  succeeded without a valid nonce (not deliberately tested adversarially,
  that's Persona 4/5's job, just noting it never got in the way of the
  honest path either).
- Both challenges' solve counts (`solves: 5` → `7` as I solved them)
  confirm other concurrent personas legitimately solving the same
  challenges at the same time with no cross-contamination of my own
  solve/session state observed.
- No symptoms of round 1's Finding 2 (network egress / cross-instance
  reachability) noticed incidentally during this run — not investigated,
  per instructions, just noting absence isn't evidence of absence given I
  wasn't looking.
