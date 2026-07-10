# Playtest Feedback, Round 2 (notes, capturing before executing)

Real feedback from the user testing the live CTFd instance after Stages
1-6 of the previous round landed. Captured verbatim intent before acting.

**Status: capturing + triaging. Concrete UI fixes below are being executed
immediately; the hint/description redesign is bigger and gets a sample
first.**

## 1. The running CTFd instance has stale (pre-Stage-4) content

The user saw `"Launch your Bandit environment from this challenge
(shared across all Bandit levels -- launching any one of them starts the
same persistent box) and connect via SSH to the host/port CTFd shows you
once it's ready."` in a challenge description — that's the OLD
`CONNECT_NOTE` text from before Stage 4's rewrite. Stage 4-6's work
regenerated `challenge.yml` files in `CEI-Labs-Wargames`, but those were
never re-synced into the actually-running CTFd test instance (no
`ctfcli`/`challenges-load.sh` re-run happened after Stage 4). The
database still has whatever was loaded much earlier in the session.
**Needs a real re-sync before any further playtesting is meaningful.**

## 2. Relaunch hit `"orchestrator returned 500: internal error creating instance"`, then self-resolved

Consistent with two already-documented, already-known issues from the
previous round (see `docs/self-hosted-wargames-status.md`): the flaky
teardown+recreate network race, and/or the 2-gunicorn-worker state-sharing
bug. Not a new bug — expected behavior given what's already documented,
not re-investigated here.

## 3. The launch panel's connection info is too verbose

Currently (`launch.html` / `challenge-launch.js`) shows, every time:
```
Connect via SSH:
ssh operator@ctf.local -p 32002
Connects via any swarm node — this hostname routes to whichever node the
target actually landed on.

Shared environment: bandit — launching from any level in this track
reuses the same box.
```
User wants **minimal** — just the actual connection info, no explanatory
prose repeated on every single view. Explicitly: it should look more like
OverTheWire's own site, which is terse (just host/port/username/password,
no essay).

## 4. Description format should match OverTheWire's actual page structure

User pasted OTW's real wargames site content (Bandit/Krypton/Natas markdown
source) as the reference. Real OTW pages follow a consistent structure:
- **Level Goal** — one short paragraph, just the task
- **Commands you may need to solve this level** — a flat list of tool
  names (many linked to manpages)
- **Helpful Reading Material** — a short list of links to background
  reading (Wikipedia, etc.) for concepts the level assumes

Our current descriptions (Stage 4's rewrite) are closer to this than the
pre-Stage-4 CONNECT_NOTE version, but don't have the distinct
Goal/Commands/Reading structure OTW uses, and don't link out to any
reference material at all.

## 5. Hints need a real 3-tier "crawl, walk, run" system, not one hint per level

This is the big one. Current state (Stage 6): one hint per level, one
cost, written from memory. User wants three tiers per level instead:

- **Tier 1 ("crawl"):** essentially just links to the relevant tool
  docs/manpages — matching OTW's own "Commands you may need" list.
  **Minimal cost, near-free** ("minimal points removed if any for
  unmasking them").
- **Tier 2 ("walk"):** a real explanation of the technique/approach —
  more than a pointer, not yet the full answer. Cost should be
  substantial: **at least half the challenge's total point value.**
- **Tier 3 ("run"):** "almost a writeup on how to answer the question" —
  pulled from real internet writeups and condensed into one clear,
  approachable explanation. Explicitly should NOT read as condescending —
  the stated goal is "exposure, learn, and grow as a professional," and
  hints should never make a player "feel stupid for not knowing."

This requires actually researching real writeups online (WebSearch/
WebFetch) for at least the less-obvious levels, not just writing from
memory — the user's instruction is specific: "use the internet to pull
writeups and condense them down."

**Scale: 56 real levels × 3 hints = up to 168 hint entries.** Given the
size and the partly-subjective "does this feel right" nature of the
writing (tone, how much to reveal at each tier), doing a small sample
first (a level or two per track) for the user to sign off on before
committing to all 56 is the plan — see status doc / commit history for
what actually shipped.

## Correction to earlier session notes: the repeated "403 on DELETE" was never a CTFd quirk

Multiple points earlier in this session, a `DELETE /api/v1/challenges/<id>`
or `DELETE /api/v1/hints/<id>` call returned 403 despite a valid admin
session and a correct `CSRF-Token` header, and was written off each time
as "not worth chasing" / minor test-hygiene noise. Root-caused for real
while cleaning up duplicate hints during the 3-tier hint rollout: CTFd's
global CSRF check (`CTFd/utils/initialization/__init__.py`) branches on
`request.content_type` -- if it's exactly `application/json`, it checks
the `CSRF-Token` header; for anything else (including a bodyless request
with NO `Content-Type` at all, which is what a plain `curl -X DELETE` or
a bare `requests.delete()` call sends by default), it instead checks for
a form-encoded `nonce` field, which a header-only request never has, so
it 403s. Adding `-H "Content-Type: application/json"` (even with an
empty body) fixes it every time. Not a CTFd bug or a real permissions
gap -- purely a client-side gap in how these one-off admin scripts were
built. Worth remembering for any future direct-API scripting against
this CTFd instance.

## Execution order

1. Fix item 3 (launch panel verbosity) — small, unambiguous, no research
   needed. `cei-labs-engine`.
2. Note item 2 as already-understood, no action.
3. Re-sync the live CTFd test instance with Stage 4-6's actual current
   content (item 1) so further playtesting reflects real work, not stale
   state.
4. Build one full sample per track (Bandit, Krypton, Natas — one level
   each) in the new OTW-style description format + 3-tier researched
   hints, show the user, get sign-off before scaling to all 56.
