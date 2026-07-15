# Adversarial Persona Testing: Round 1 Findings

Five subagent personas (see `adversarial-persona-testing.md` and
`adversarial-persona-briefs/`) run concurrently against a live stress-test
deployment on 2026-07-12/13 (`cei-labs-engine@81da136`, before the fixes in
this document). Two real, high-severity infrastructure bugs were found and
fixed same-day (`cei-labs-engine@65317ce`); everything CTFd-security-layer
related held correctly under real attack attempts.

## Finding 1 (CRITICAL, fixed): relaunch on an active instance was 100% broken

Found independently by **two personas** (Confused Novice and Impatient Power
User) — every single "Relaunch Environment" attempt against an already-
running instance returned `orchestrator returned 500: internal error
creating instance`, with no self-service recovery. "Reboot Host" (restart-
in-place, a different action) worked reliably every time — the bug was
specific to teardown-then-recreate, not general instance-lifecycle
unreliability.

**Root cause**: `docker_client.py`'s `remove_service()`/`remove_network()`
returned as soon as the Swarm API *accepted* the removal request, without
waiting for the underlying task to actually finish draining (stop, detach
from its networks). `remove_network()`'s own pre-existing comment admitted
this ("Network may still be draining a just-removed service's endpoint; the
reaper/caller is expected to retry on next sweep") but the relaunch path
doesn't wait for a reaper sweep — it immediately tries to recreate with the
identical service name, network name, and port, racing the still-draining
old task.

**Fix**: `remove_service()` now polls the removed service's task state
(via the low-level task-list API, filtered by service ID) until every task
reaches a terminal state, bounded at 15s. `remove_network()` now retries
removal for the same window instead of a single attempt. See
`docker_client.py`'s updated docstrings for full detail.

## Finding 2 (CRITICAL, confirmed — fix attempted and reverted, still open): broken network airgap — real egress + real cross-instance reachability

Two personas independently found different symptoms of the **same root
cause**:

- **Impatient Power User → confirmed cross-team exposure**: after a failed
  relaunch, the freed port CTFd's own status API reported as dead was still
  live, serving what appeared to be a *different team's* instance (different
  container hostname, different flag string).
- **Account & Answer Cheater → confirmed cross-instance reachability**: from
  inside their own legitimately-launched, supposedly-airgapped container,
  found a live, unrelated SSH host answering at a consistent network offset
  (`.2`) on **two independently-provisioned instances** on two different
  subnets. Full port sweep ruled out this being the orchestrator's own API
  (only port 22 answered). Correctly stopped short of authenticating to it
  (blocked by this session's own safety tooling, matching the engagement's
  hard-stop rule) — could not conclusively identify whose container it was,
  which is itself consistent with either interpretation below.
- **AI-Assisted Infrastructure Breaker → confirmed real internet egress**:
  `curl https://1.1.1.1` succeeded (HTTP 301) from inside a container on a
  network configured `internal: true` — direct contradiction of the "real
  airgap, no outbound route at all" design claim. Traced to 3 network
  interfaces existing where only 1 was intended (the container's own
  overlay network plus Swarm's `ingress` network plus `docker_gwbridge`).

**Root cause**: `docker_client.py`'s `create_service()` published ports
using Docker Swarm's *default* `vip` (routing-mesh) mode, which implicitly
attaches every such service to the shared `ingress` network regardless of
what other networks it explicitly requests. On this deployment's single-node
topology, that shared `ingress`-mediated NAT path gives the host's own
routing knowledge of *every* overlay subnet a way to leak through — both as
outbound internet access (explaining Finding 2's egress) and, combined with
Finding 1's incomplete network cleanup (a stale, un-drained container from a
prior launch left sitting on a network whose name got silently reused),
plausibly explains the "mystery `.2` host": most likely an orphaned
container from an earlier launch, reachable via the same ingress-mode side
channel, not a live breach of a currently-active other team's instance. This
is the leading hypothesis, not confirmed with certainty — round 2 (post-fix)
re-testing should specifically try to reproduce it.

**Fix attempted, then reverted**: published ports were switched to
`PublishMode="host"` instead of the default `vip` mode, which should bind
directly on the node without joining `ingress`/`docker_gwbridge` at all.
Live-verified this does correctly stop the leak in principle, but also
**confirmed live that Swarm host-mode publishing silently binds nothing at
all on this specific deployment** — the Engine API accepts and reports back
the host-mode endpoint spec, but no listener ever appears on the host
(`ss -tlnp` empty). Reproduced independently of this codebase with a bare
`docker service create --publish mode=host` against a stock `nginx` image,
ruling out anything orchestrator-specific. This made every single-target/
range-attacker instance completely unreachable — a worse regression than
the leak itself — so the change was reverted back to `vip` mode with the
operator's explicit sign-off. **Finding 2 remains open.** Root cause of the
leak itself is still believed correct (see above); root cause of *why host
mode doesn't bind on this box* is not yet identified and needs isolated
investigation (Docker Engine version quirk vs. daemon config gap) before
host mode is viable here. Finding 1's fix (stale containers now actually
get cleaned up before a network name is reused) is unaffected by this
revert and remains in place — 8/8 back-to-back relaunches verified working
both before and after the Finding 2 revert.

> **Resolution update for Finding 2 (2026-07-14):** the trusted-gateway
> redesign removes all published ports and shared overlays from untrusted
> workloads. A fresh bare Swarm smoke test on the current station successfully
> scheduled `nginx:alpine` with `mode=host`, bound node port 32999, and returned
> HTTP 200. Engine therefore now uses host publish mode only on its hardened
> TCP gateway. This also avoids a newly observed failure mode in the old
> routing-mesh path: the `/24` ingress allocator reported no available IPs
> after repeated launch/delete testing even though network inspection showed
> only two visible endpoints. The gateway live isolation suite is the release
> gate for this fix; the historical failed attempt above is retained as
> context.

## Finding 3 (documentation gap, not yet fixed): SSH `connect_host` doesn't resolve

Baseline Participant found that `single-target`'s `connect_host` field
returns the bare `BASE_DOMAIN` (`ctf.local`), which — unlike CTFd's own
`ctfd.ctf.local` — has no `Host:`-header workaround for a raw SSH client.
Real participants need this resolvable (via `/etc/hosts` or real DNS) with
no browser-side trick available. Tracked as a real gap, not yet fixed —
needs a decision on local DNS setup (see TRACKER.md §4) or clearer
`docs/participant-quickstart.md` guidance (which doesn't currently exist in
this repo — see Finding 5).

## Finding 4 (low severity, tracked): `no_new_privileges` still unset

Re-confirmed on real Swarm hardware (previously only checked as a known
code-level gap). SUID root binaries exist in the target image, but `su`
correctly demands a password the attacker doesn't have, and even a
hypothetical root shell stays bounded by the container's narrow capability
set. Low exploitability today; still an open item per TRACKER.md §2 (a
docker-py SDK limitation, no fix available through this client).

## Finding 5 (documentation gap): referenced participant docs don't exist

Two personas' briefs cited `docs/participant-quickstart.md` as the
authoritative reference for the expected participant journey. It doesn't
exist anywhere in `cei-labs-engine` — the real file lives in the separate
`CEI-Labs-Wargames` repo. Cross-repo doc reference error in the persona
briefs themselves (now know to fix), and a reminder that this repo has no
independent participant-facing quick-start of its own.

## Minor findings (expected-correct or low-priority, not fixed)

- Two inconsistent rate-limit response shapes exist for flag-submission
  throttling (a friendly JSON response with countdown vs. a blunt
  plain-text 429) — cosmetic, both correctly throttle.
- `action` field on lifecycle endpoints isn't validated — an unknown string
  silently no-ops to the default action instead of erroring.
- `/rules` returns 404.
- `docker/orchestrator/tests/` capacity/CPU-limit gap (no CPU/pids limit
  enforcement) was code-confirmed but deliberately not live-fired (fork-bomb
  risk to the shared host mid-round) — flagged for isolated re-testing, not
  a round-1 finding in itself.

## What held correctly (extensive — Account & Answer Cheater, ~20 real attack attempts)

CSRF protection on both flag submission and instance launch (403 without a
valid token), real rate limiting on `/api/v1/challenges/attempt` (burst-of-5
then 429, not decorative), session tampering / stale cross-persona cookie
replay rejected, IDOR-proofing on instance-launcher routes (client-supplied
owner_id/account_id overrides ignored, server always uses the caller's real
identity), all `/admin/*` routes and the shared-secret sync endpoint
correctly gated, the per-team dynamic flag system fails closed on a missing
`TeamChallengeSecret` row (a genuinely-correct-but-unearned flag value is
still rejected), no flag values leak in any API response, and the
orchestrator's internal hostname doesn't resolve from inside a challenge
container (no Docker socket mounted either).

## Per-persona summary

- **Baseline Participant**: full journey completed in ~8m22s (scripted).
  Hit Finding 1/2's precursor symptoms (transient 500, unresolvable
  connect_host) but every core mechanic (CSRF, teams-mode gating, dynamic
  flags, reboot recovery, session persistence) worked cleanly.
- **Confused Novice**: every "honest mistake" scenario handled gracefully
  except Finding 1 (relaunch) and its Finding-2-adjacent side effect
  (stale port serving another team's data).
- **Impatient Power User**: isolated the relaunch bug precisely (100%
  failure rate on relaunch-of-active, 0% on general concurrent load across
  *different* challenges — 150+ requests, no failures) and proved a real
  orphaned-but-reachable container via a 15-request concurrent burst.
- **Account & Answer Cheater**: one real infrastructure finding (Finding 2's
  cross-instance reachability symptom); every CTFd-application-layer attack
  attempted was correctly blocked.
- **AI-Assisted Infrastructure Breaker**: no host-level access, no
  confirmed other-team data reached (hard stop never triggered). Found
  Finding 2's egress-bypass symptom, confirmed memory limits work (OOM-
  killed cleanly, Swarm auto-recovered in ~8s), confirmed Docker
  socket/host filesystem/DB/Redis/orchestrator API all correctly
  unreachable.

## Next steps

1. **Finding 1 is fixed and live-verified** (`cei-labs-engine@c111fbd`, after
   a same-day follow-up fix to the fix itself — see the docker_client.py
   history around that commit): 8/8 back-to-back relaunches against a real
   active instance succeeded, confirmed twice (once immediately after the
   fix, again after Finding 2's revert below).
2. **Finding 2 is still open** — the host-mode fix was reverted
   (`cei-labs-engine@2fe010e`) after confirming it breaks all instance
   connectivity on this deployment. Needs a different fix approach:
   isolate why Swarm host-mode publishing doesn't bind here, or find an
   alternative to routing-mesh publishing entirely for challenge
   containers that need a strict airgap (e.g. Traefik TCP routing instead
   of a direct published port, if that turns out more reliable). Not
   blocking round 2 as long as round 2's personas are briefed that this
   specific leak is a known, accepted-for-now gap rather than something
   to keep re-discovering.
3. Round 2: rerun with 10 personas (two of each type) per
   `adversarial-persona-testing.md`'s escalation plan — Finding 1 being
   fixed is enough to proceed; Finding 2 doesn't need to block it.
4. Findings 3 and 5 (doc gaps) still need resolution — not blocking round 2.
