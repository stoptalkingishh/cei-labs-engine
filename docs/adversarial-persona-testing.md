# Adversarial Persona Testing

Complements `hint-tier-persona-findings.md` (content/UX personas) with a
security- and capacity-focused counterpart: five AI subagent personas driven
against a live deployment to satisfy TRACKER.md §6 ("Repeat isolation/
security tests under concurrency, including attempts to enumerate or access
another participant's services, flags, network, volumes, and CTFd identity")
and §7 ("Confirm the vulnerable Web track cannot attack CTFd, orchestration
APIs, the host, router/APs, other teams"), neither of which had been
exercised by anything other than manual spot-checks before this framework
existed.

## Why subagents, not scripts

A scripted attacker only finds what its author thought to script. These
personas are full agent sessions with real tool access (HTTP client, SSH,
browser-equivalent), given a role and a goal, not a fixed sequence of
requests — closer to what a red team or a genuinely motivated participant
would actually do, including improvising after an initial attempt fails.
Deterministic load (concurrency, capacity, throughput numbers) still belongs
to `docker/orchestrator/tests/` and the load harness pattern used in
TRACKER.md §6's staged runs — personas are for behavior a script wouldn't
think to try, not for measuring latency percentiles.

## Global rules of engagement (apply to every persona)

These are the outer boundary. A persona's own left/right limits (below)
narrow this further; nothing in a persona's brief ever widens it.

**Right limit — explicitly in scope:**
- Everything reachable through the persona's own legitimately-issued
  CTFd account and whatever the orchestrator hands back as connection
  info for instances *that account* launched.
- Attempting privilege escalation, container escape, cross-team access,
  API abuse, and infrastructure attacks *that stay within* the target
  deployment (the box under test and the containers/networks it creates).
- Using AI assistance at full speed — that's the point of Persona 5.

**Left limit — never in scope, no exceptions:**
- Nothing outside the target deployment's own LAN-exposed surface. No
  scanning, probing, or connecting to any other host, the control
  station running the test, or the public internet from inside a
  challenge container, beyond what's needed to *demonstrate* an egress
  finding (prove one packet gets out, don't go browse the web from
  there).
- No irreversible destructive action beyond what's needed to *prove* a
  finding. Getting root on the Docker host and demonstrating you *could*
  run `rm -rf /` is a finding; actually running it is not — that
  destroys the evidence and the rig everyone else is testing against.
- No real credentials, real personal data, or real payment/PII flows —
  test accounts and test data only.
- No denial-of-service against shared infrastructure (this station, the
  LAN, upstream DNS) — resource exhaustion *of the target CTF stack
  itself* is in scope (that's a real capacity finding), exhausting
  something outside it is not.
- Every action gets reported, not just the ones that worked. A finding
  that "X is blocked correctly" is exactly as valuable as one that
  succeeds — silence on failed attempts hides exactly the coverage this
  exercise exists to provide.

## Methodology

1. Fresh, isolated CTFd accounts per persona (`persona_<name>`, matching
   the existing `hint-tier-persona-findings.md` naming convention),
   never reusing an account across rounds so findings can't be muddied
   by leftover state.
2. Each persona is a separate `general-purpose` subagent with real Bash
   tool access, briefed from its file under `docs/adversarial-persona-
   briefs/` (self-contained — the brief alone is enough context to run
   the persona without anything else from this session).
3. Personas run concurrently, matching how real participants would
   actually load the system, not sequentially.
4. Each persona writes its own findings to a dedicated scratch file;
   findings get consolidated afterward into a single dated report (see
   "Reporting format" below) rather than five separate untracked
   transcripts.
5. Between rounds, the stack gets reset to a known state (`docker stack
   rm` + `stack-up.sh`, or at minimum `scripts/status.sh` plus a check
   for orphaned instances via `/admin/instances`) so round N's leftover
   state can't mask or fake round N+1's findings.

## The five personas

| # | Persona | Category | Primary question it answers |
|---|---|---|---|
| 1 | Baseline Participant | Honest usage | Does the happy path actually work end to end? |
| 2 | Confused Novice | Honest usage, error-prone | Does the system survive normal human mistakes gracefully? |
| 3 | Impatient Power User | Honest usage, high-throughput | Do rate limits/idempotency hold under aggressive-but-legitimate use? |
| 4 | Account & Answer Cheater | Adversarial, CTFd-focused | Can a participant get flags/access they shouldn't have? |
| 5 | AI-Assisted Infrastructure Breaker | Adversarial, platform-focused | Can a participant escape their sandbox and attack the platform or other teams? |

Full briefs: `docs/adversarial-persona-briefs/01-baseline-participant.md`
through `05-ai-infrastructure-breaker.md`.

Personas 1–3 exist because an adversarial-only test answers "can this be
attacked" but not "does this work for the 95% of participants who are just
here to learn" — both questions matter for an event-readiness sign-off, and
several of the mechanical edges Persona 3 stresses (rate limits, idempotent
reset/launch) are the *same* code paths Persona 5 tries to abuse, just
without malicious intent. A capacity or correctness bug that only shows up
under "impatient but honest" load is arguably worse than one that only
shows up under attack, since it'll happen on event day with zero adversary
required.

## Escalation: round 2 (10 personas)

Once round 1 passes clean (or its findings are fixed and re-verified), rerun
with **10 personas — two independent instances of each of the five types**,
run concurrently, on the same deployment. This isn't "run it twice" — two
`persona_cheater` instances racing each other for the same admin-panel
timing window, or two `persona_breaker` instances both attempting container
escape against the same orchestrator at once, exercises concurrency paths a
single instance of each persona structurally cannot reach (this is exactly
the shape of bug the multi-worker race condition fix addressed — see
`security-audit-status.md` — so doubling up adversarial personas is a
deliberate attempt to find the next one, not just more of the same
coverage). Name the second instance of each type `persona_<name>_2` to keep
accounts distinguishable in CTFd/logs.

## Reporting format

One dated file per round: `docs/adversarial-persona-findings-round-N.md`,
following `hint-tier-persona-findings.md`'s existing format — numbered
findings, each with real evidence (exact request/response, log excerpt, or
transcript quote — not a paraphrase), a verdict (real bug / expected-and-
correct / test artifact), and a per-persona summary section at the end.
Findings that represent real security or correctness bugs get a matching
entry in `../../cei-labs-event/TRACKER.md`'s risk register, same as the
multi-worker race condition fix.

## Safety notes for whoever runs this

- Confirm the deployment under test is the stress-test rig, not a real
  event instance, before starting — check `docker/.env`'s `BASE_DOMAIN`
  and that no real participant accounts exist yet.
- Watch for a persona that stops reporting progress — that's the signal
  to check in on it directly rather than assume it's still working
  quietly; an agent that's stopped narrating is either done, stuck, or
  has wandered outside its brief.
- `docker/admin/instances` and `/admin/ranges` (via `X-Admin-Auth`) give
  a live view of everything currently provisioned — check this
  periodically during a run to catch orphaned instances or unexpectedly
  high counts before they become a capacity problem for other personas.
