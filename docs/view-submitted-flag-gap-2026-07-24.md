# No way for a player to view a previously-submitted flag (2026-07-24)

Requested: after solving a challenge, a player should have the *option* to
see the flag/answer they submitted for it (not auto-displayed — an
explicit reveal, on demand). Notes only, no code changed.

Practically motivated by these tracks' own design: Bandit/Krypton/Natas
progression requires the previous level's password to log back in as the
next account (SSH) or authenticate to the next page (Natas) — if a player
reconnects later (new SSH session, browser restart) and has forgotten a
password they already earned, there's currently no way to look it back up
short of re-solving the level from scratch.

## Confirmed: the data exists, there's just no player-facing way to read it

Checked directly against the built `cei-labs-engine` CTFd image (3.8.2,
`docker/ctfd/Dockerfile`):

- `CTFd/models/__init__.py:871` — `Submissions` model has a
  `provided = db.Column(db.Text)` field storing the exact text a player
  submitted for every attempt (correct or not).
- `CTFd/models/__init__.py:925` — `Solves(Submissions)` is CTFd's own
  subclass for specifically-correct submissions, inheriting `provided`.
  So every solve already has its exact submitted flag sitting in the DB.
- `CTFd/api/v1/submissions.py` — **every** route in this file
  (`SubmissionsList`, `Submission` detail, etc.) is decorated
  `@admins_only`. There is no player-facing CTFd core endpoint, stock or
  otherwise in this codebase's plugins, that lets an authenticated
  non-admin read back their own `provided` value for a solve they own.

This isn't a bug in anything CEI Labs built — it's stock CTFd's own
by-design behavior (submissions are an admin-only audit trail, not a
player-facing feature). Closing this gap means adding new functionality on
top of CTFd, not fixing something broken.

## What implementing this would need

Not attempted here, but scoped enough to hand off:

- **A new authed-only API route** (pattern already established in this
  codebase — `hint-wallet` and `instance-launcher` both add their own
  `/plugins/<name>/api/...` routes under `@authed_only` rather than
  touching CTFd core) that looks up the CURRENT user/team's own `Solves`
  row for a given `challenge_id` and returns its `provided` value. Must
  scope strictly to the requesting user/team's own solve — never another
  team's, and never for a challenge they haven't personally solved (a
  straightforward `Solves.query.filter_by(challenge_id=..., account_id=...)`
  matching the `owner_id`/team-vs-user convention `hint-wallet` and
  `instance-launcher` already use).
- **A UI affordance in the challenge modal**, off by default — a "Show my
  submitted answer" button/toggle a player clicks, not something that
  renders automatically on solve or on reopening a solved challenge. The
  existing `modal-theme` plugin (`docker/ctfd/plugins/modal-theme/`) is
  already the thing that customizes CTFd's challenge-modal appearance and
  would be the natural place to add the reveal control, following the
  same injection pattern `hint-wallet`'s and `instance-launcher`'s modal
  scripts already use.
- **A decision on scope**: only the currently-open challenge's own
  submission, or a "my solved challenges" list/lookup a player could check
  without reopening each challenge modal individually (more useful for the
  actual motivating case — needing an old Bandit/Krypton password back —
  but more work).
- **A decision on whether this should be event-wide default-on, or an
  admin-configurable toggle** (some CTF organizers deliberately keep this
  hidden to discourage flag-sharing screenshots/write-ups during a live
  event; CEI Labs' own tracks have a different incentive since the
  "flag" often doubles as a login credential you may legitimately need
  again).
