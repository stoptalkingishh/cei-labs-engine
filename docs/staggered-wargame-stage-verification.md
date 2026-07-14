# Staggered wargame stage verification

This checklist verifies the Bandit, Krypton, and Natas administrator controls described in [staggered-wargame-stages.md](staggered-wargame-stages.md).

## Automated checks

Run from the Engine repository root:

```bash
python -m unittest discover docker/ctfd/plugins/wargame-stages/tests -v
python -m compileall -q docker/ctfd/plugins/wargame-stages
```

The unit suite covers timestamp immutability, idempotent start and lock actions, invalid transitions, inclusive start/lock boundaries, deterministic ties, overlapping active games, and exclusion of all solves before a game starts.
It also verifies that spreadsheet-formula prefixes in participant/team names are neutralized in CSV exports.

## Deployment smoke test

1. Build and start the CTFd image, sign in as an administrator, and open **Wargame stages**.
2. Load all Wargames challenges, then press **Sync** for each game. Confirm exactly `35/35`, `8/8`, and `16/16`. A start with any other count must return HTTP 409.
3. Confirm all three pending games are hidden from a participant. Start Bandit and record its displayed UTC time. Confirm only Bandit challenges become visible.
4. Press Start again using a second browser session. Confirm the original time is unchanged and the audit table has only one `start` mutation.
5. Solve Bandit as two participants. Confirm score order is points descending, then elapsed time from the Bandit start, then name.
6. Start Krypton while Bandit remains active. Confirm both are playable and each scoreboard contains only its own category.
7. Hide Bandit's scoreboard. Confirm participants receive 404 but administrators still see it; show it again and confirm all scores are unchanged.
8. Lock Bandit, record the cutoff, then submit another Bandit solve. Confirm CTFd accepts the learning solve but the Bandit score does not change.
9. Close Krypton while active. Confirm its close time is also its scoring cutoff. Start Natas and confirm its clock begins at its own start time, not the event or Bandit start.
10. Restart CTFd. Confirm states, timestamps, mappings, visibility, scoring cutoffs, and audit rows persist.

## Bug and failure injection

- Rename one challenge category and sync: mapping count must fall below expected and Start must be blocked.
- Attempt to map a challenge to two games: sync must fail with HTTP 409.
- Submit solves one microsecond before start, exactly at start, exactly at cutoff, and one microsecond after cutoff. Only the middle two count.
- Issue simultaneous Start requests. The database row lock and immutable `started_at` must produce one start time.
- Hide/show a scoreboard repeatedly. No solve, mapping, state, or timestamp may change.
- Test both CTFd user and team modes; hidden or banned accounts must never appear.
- Back up the database before event day and verify restoration in a disposable environment.

## Future multi-participant stress test

The next load run must simulate at least the planned attendance plus 50 percent headroom. Participants should concurrently load challenge lists, submit correct and incorrect flags, poll all visible scoreboards, and launch environments while an administrator starts a second game and locks the first. Capture request latency and error rate, MariaDB connections/locks, CTFd worker CPU and memory, orchestrator queue depth, container count, host CPU/memory/disk/network, and the exact audit/timestamp results. The pass condition is no duplicate start, no cross-game solve leakage, no post-lock score movement, no missing accepted solve, and no sustained resource saturation or elevated error rate.

## Known scoring scope

Stage scoreboards intentionally total mapped challenge values only. Global CTFd awards and paid-hint deductions are not assigned to a game and therefore are not included. If paid hints are enabled for these games, add explicit per-game hint attribution before calling the scoreboard equivalent to CTFd's global score.
