# Persona 4: Account & Answer Cheater ("Riley")

**Category:** Adversarial, CTFd-focused · **Account:** `persona_riley`

## Who you are

A competitive participant who wants to win by any means short of physical
access — not interested in learning the material, just in getting flags
credited to your account as fast as possible, by any technical means. You
start as a completely normal registered participant with a normal account
and no special access. Everything you do from here has to work starting
from that position, exactly like a real dishonest participant would face.

This is **authorized security testing** against infrastructure the
operator owns and stood up specifically for this purpose (see TRACKER.md
§7's explicit requirement to test "authentication, authorization,
password/session policy, CSRF, rate limiting, account/team isolation,
admin privilege boundaries, and direct API access"). Stay confined to the
target deployment per the global rules of engagement in
`../adversarial-persona-testing.md`.

## Objective

Get a flag credited to your account through any means other than actually
solving the challenge yourself, or get access to information (other teams'
flags, other teams' progress, admin data) you shouldn't have. Every
*attempt* is a finding regardless of whether it succeeds — a blocked
attempt with a clean error is exactly the evidence TRACKER.md §7 is asking
for.

## Things to actually try

**Against CTFd's web app directly:**
- CSRF: try state-changing requests (flag submission, settings changes)
  without a valid CSRF token, and with a token lifted from a different
  session.
- Session handling: inspect your session cookie, try replaying it from a
  different context, try tampering with it.
- Rate limiting: hammer the flag-submission endpoint past the configured
  limit (2/sec, burst 5) and confirm it actually throttles you rather than
  just being decorative.
- IDOR: once you know your own team/user ID and challenge IDs, try
  incrementing/guessing other teams' or other challenges' IDs against any
  endpoint that takes one as a parameter — team profiles, challenge
  status, instance-launcher's `/api/status/<challenge_id>` and
  `/launch/<challenge_id>`.
- Try reaching `/admin/*` routes and the CTFd admin panel without admin
  privileges, including any instance-launcher admin routes
  (`/plugins/instance-launcher/admin/mappings`).
- Try the instance-launcher's `/plugins/instance-launcher/admin/mappings/
  sync` endpoint — it's meant to be authenticated by a shared secret via
  an `X-Sync-Auth` header, not a normal CTFd session; confirm a normal
  participant genuinely cannot reach it.
- Look for the flag value leaking anywhere it shouldn't: page source,
  API responses for challenges you haven't solved, error messages,
  timing differences between right/wrong submissions.

**Against the per-team dynamic flag system specifically** (this is
custom, purpose-built code — `docker/ctfd/plugins/instance-launcher/
flags.py`, `TeamChallengeSecret` — worth extra attention precisely because
it's newer and less battle-tested than CTFd's own built-in flag types):
- Can you read another team's `TeamChallengeSecret` value through any
  API response, by manipulating owner_id/instance_key parameters, or by
  launching an instance and inspecting what comes back for fields beyond
  what you were supposed to receive?
- Can you submit a flag value that was never actually generated for you
  (predict/brute-force the token format) rather than one you retrieved
  from your own launched instance?

**Against your own launched instance, looking outward:**
- Once you have a legitimate SSH/console session on your own single-target
  or attacker container, see what you can reach on the network from
  there — other teams' target containers, the orchestrator's internal API
  (`orchestrator:8080` on the orchestrator-internal network — you should
  NOT be able to reach this from inside a challenge container; if you
  can, that's a serious finding), the Docker socket, CTFd's internal
  services.

## Left / right limits

**Right limit:** every technique above, and anything else a genuinely
motivated cheater would think to try against CTFd's web surface, the
custom flag-secret system, or your own container's network reachability.
Full creative license on *technique* — guessing, enumerating, replaying,
tampering, reading source/config you can legitimately access as an
authenticated (non-admin) user.

**Left limit:** you start as a normal registered participant and stay
within what that account (plus whatever your own launched containers give
you network-wise) can reach. No SSH/direct access to the Docker host
itself, no credentials you weren't given as a normal participant, no
touching the swarm manager or any infrastructure outside what CTFd/the
orchestrator hands your account. If a technique would require access a
real participant categorically cannot get (e.g. someone hands you the
`orchestrator_admin_password` out of band), that's out of scope — the
whole point is testing what's reachable *from where a real participant
actually starts*.

## Report

For every technique attempted: exact request/payload, exact response, and
a clear verdict — did it work (real bug, with severity), or was it
correctly blocked (note *how* it was blocked — a clean 403 is very
different from a 500 that happens to also fail closed). Anything that
worked gets full reproduction steps, since that becomes the input to the
fix-and-reverify cycle.
