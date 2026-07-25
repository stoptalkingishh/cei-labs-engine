# Staggered Wargame Stages

## Purpose

CEI Labs runs Bandit, Krypton, and Natas as three independently started
wargames inside one CTFd event. This is a staggered release model, not a
forced transition: starting Krypton does not automatically close Bandit,
and starting Natas does not automatically close either earlier game.

Each stage has its own start-relative scoreboard. Participants may continue
an earlier game while a later game is open unless an administrator explicitly
locks that stage.

## Default stages

| Order | Slug | Display name | CTFd category | Expected content |
|---:|---|---|---|---:|
| 1 | `bandit` | Bandit — Linux Basics | `Linux Basics` | Start Here + levels 0–33 |
| 2 | `krypton` | Krypton — Cryptography | `Cryptography` | Start Here + levels 0–6 |
| 3 | `natas` | Natas — Web Security | `Web Security` | Start Here + levels 0–14 |

The Wargames repository's `game-stages.yml` is the content-side source of
truth. The Engine administrator sync matches imported CTFd challenges by
their exact category and records explicit challenge-to-stage mappings.

## State model

- `pending`: not started; mapped challenges are hidden from participants.
  This is enforced automatically — every challenge in a `pending` stage's
  category is mapped and forced to CTFd's `hidden` state (a) on every CTFd
  app start (container restart/redeploy) and (b) via the
  `/plugins/wargame-stages/machine/reconcile` endpoint, which
  `CEI-Labs-Wargames/deploy.sh` calls automatically as the last step of
  every content push. There is no manual step required to keep challenges
  hidden — the admin "Re-check mapping" button (formerly "Sync") is now
  just a manual on-demand trigger for the same reconcile logic, useful for
  confirming mapped counts, not the thing that makes hiding happen.
- `active`: challenges are visible and eligible solves count toward the
  stage scoreboard.
- `locked`: challenges remain available for review, but new solves do not
  enter the stage scoreboard after `locked_at`.
- `closed`: final administrative state after results are verified/exported.

Scoreboard visibility is independent from scoring state:

- `visible`: participants can view the stage standings.
- `hidden`: only administrators can view standings. Hiding never deletes
  solves or changes scores.

Only administrators may synchronize mappings, start, lock, close, or change
scoreboard visibility. Multiple stages may be active simultaneously.

## Scoring contract

1. A stage's clock starts at its immutable first `started_at` timestamp.
2. Only solves for challenges explicitly mapped to the stage count.
3. Only solves with `solve.date >= started_at` count.
4. For a locked or closed stage, only solves at or before the effective
   cutoff (`locked_at` or `closed_at`) count.
5. Ranking is total stage points descending, then last scoring solve elapsed
   time ascending, then account name for deterministic display.
6. Elapsed time is measured from this stage's `started_at`, never from the
   global CTFd event start and never from another stage.
7. The stock global CTFd scoreboard is not rewritten. The plugin provides a
   separate scoreboard for each wargame and an optional overview.
8. Hiding a scoreboard changes presentation only. Locking freezes which
   solves count. Closing marks results final. No operation deletes solves.

## Administrator workflow

### Before participants arrive

1. Import all Wargames challenges — `CEI-Labs-Wargames/deploy.sh`
   automatically hides everything mapped to a not-yet-started stage as its
   last step; no separate action is required for this.
2. Open **Admin → Wargame Stages** (a themed page within the normal CTFd
   admin interface, not a separate unstyled panel).
3. Use "Re-check mapping" to verify expected challenge counts if anything
   looks off — hiding itself already happened during import.
4. Confirm all three stages are pending and participant scoreboards hidden.
5. Export a pre-event mapping snapshot.

### Bandit start

1. Announce the rules and Bandit start.
2. Start Bandit in the admin panel.
3. Confirm `started_at` is correct in UTC, challenges are visible, and the
   Bandit scoreboard begins empty.

### Lunch/break and later starts

At the planned break, administrators may leave Bandit active, lock it, or
hide only its scoreboard. After the break, start Krypton. Bandit remains in
its chosen state. Repeat the same process for Natas later.

### End of each stage

1. Lock scoring.
2. Verify solve counts and inspect late submissions around the cutoff.
3. Export CSV/JSON standings and retain the UTC timestamps.
4. Close the stage only after verification.

## Safety and recovery

- Starting is idempotent: repeating the start request does not reset time.
- Stage time cannot be silently restarted. Any future reset feature must be
  a separately audited, destructive workflow because existing CTFd solves
  cannot simply be submitted again.
- A challenge may belong to only one stage.
- A stage cannot start with zero mapped challenges.
- Synchronization reports missing/extra categories and never deletes solves.
- All state changes record the administrator ID and UTC timestamp.
- Lock/hide/close operations are reversible where safe; historical results
  remain available to administrators.

## Verification and stress testing

The feature is not event-ready until all of the following pass:

- Unit tests for time-window inclusion, cutoff handling, tie breaking,
  visibility, idempotent start, invalid transitions, and unique mapping.
- CTFd integration test covering import → sync → start → solve → lock →
  export for all three stages.
- Browser verification of participant and administrator views.
- Clock/timezone test with UTC timestamps on every node.
- Concurrent start/lock requests proving one authoritative timestamp.
- Multi-participant stress test with simultaneous solves before, at, and
  after each cutoff while two or three stages overlap.
- Score reconciliation against raw CTFd solve records after the run.

See `docs/staggered-wargame-stage-verification.md` for the executable test
matrix and evidence requirements.
