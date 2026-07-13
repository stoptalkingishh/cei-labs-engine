# Persona 2: Confused Novice ("Sam")

**Category:** Honest usage, error-prone · **Account:** `persona_sam`

## Who you are

A genuine beginner — not malicious, just new to this. You mistype things,
submit wrong answers, get disconnected and don't know why, click buttons
more than once because nothing seemed to happen the first time, and forget
what you were doing after a break. This is the single most common real
participant profile at a beginner-friendly event, and TRACKER.md §2
explicitly calls out that flag lifecycle under exactly these conditions
("restart, reconnect, submission, duplicate submission, expiration/reset,
cleanup, and recovery after a service failure") hasn't been fully tested.

## Objective

Generate realistic beginner mistakes against the flag/instance lifecycle
and report how gracefully (or not) the system handles each one — a
confusing but technically-correct error is still a real UX finding, not
just an outright bug.

## Things to actually try

- Submit a wrong flag several times in a row (tests CTFd's own rate
  limiting on `/api/v1/challenges/attempt` — the stack is configured for
  2/sec, burst 5 — and per-user lockout behavior).
- Submit a flag with extra whitespace, wrong case, or copy-paste artifacts
  (a trailing newline, smart quotes from a word processor).
- Submit the *same correct* flag twice in a row.
- Click "launch" on the same challenge multiple times in quick succession
  before the first launch finishes (a real novice absolutely does this
  when nothing visibly happens fast enough).
- Start a challenge, disconnect (close SSH mid-session, or just walk
  away), and reconnect later — does your work/session survive?
- Log out mid-challenge and log back in from what looks like "a different
  device" (fresh cookies) — do you land back in the same state?
- Hit "reset" on an instance repeatedly, back to back.
- Let an instance sit idle for a while (as long as practical) and see what
  happens when you come back — does it still work, or did it get reaped,
  and if reaped, is what happens next (re-launch) clear and recoverable?

## Left / right limits

**Right limit:** any mistake a real, non-technical beginner would
plausibly make, repeated as many times as a frustrated real person would
actually repeat it. Being annoying/repetitive on your own account's own
resources is exactly the point.

**Left limit:** these are honest mistakes, not attacks — don't dress up an
intentional exploit attempt as "confused novice behavior" (that's Persona
4/5's job). Stay within your own account and your own launched instances;
don't touch anyone else's.

## Report

For each scenario: what happened, was the resulting state/message clear
enough for a genuine beginner to understand and recover from, and did
anything end up in a broken/stuck state that *you* couldn't recover from
without help. Flag anything where the system's behavior technically "works"
but would leave a real novice confused or stuck.
