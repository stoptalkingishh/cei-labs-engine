# Engine documentation index

The `docs/` directory mixes **living documents** (kept current; trust them) with
**dated working artifacts** (session logs and fix logs that were accurate when
written and are kept as evidence/history). During an incident, only the living
docs should guide action — a stale session log read as current state is an
operational hazard.

## Living documents — current state lives here

| Doc | What it is |
|---|---|
| `architecture-decisions.md` | ADRs — why the platform is built the way it is |
| `network-prerequisites.md` | What `cei-labs-net` must provide before deploy |
| `local-testing-deployment.md` | How to bring the stack up locally |
| `offline-installation/` | Venue/air-gapped install procedure |
| `backup-and-recovery.md` | Backup/restore procedure (rehearse before event) |
| `worker-failure-recovery.md` | Recovery runbook for a failed node |
| `security-audit-status.md` | Current security posture and open items |
| `self-hosted-wargames-status.md` | Wargames integration status |
| `staggered-wargame-stages.md` | Wave-gating design for challenge release |
| `threat-model.md` | If present in your checkout: current threat model |

## Dated working artifacts — history, not current state

These were written during specific work sessions. Keep them (they are the
evidence trail the tracker cites), but do **not** read them as the current
state of the system. When they disagree with the living docs above, the living
docs win.

- `SESSION-HANDOFF-2026-07-13.md`
- `HANDOFF-2026-07-23-night.md`
- `P0-FIX-LOG-2026-07-23.md`
- `P1-FIX-LOG-2026-07-23.md`
- `validation-session-2026-07-14-15.md`
- `clean-station-restore-validation-2026-07-16.md`
- `adversarial-persona-findings-round-1.md`, `adversarial-persona-round2-findings/`,
  `adversarial-persona-briefs/`, `hint-tier-persona-findings.md`
- `playtest-feedback-round2.md`, `instance-launcher-ux-followups.md`

## Convention going forward

1. New session/handoff/fix logs get a date in the filename and a one-line
   header: *"Working artifact from YYYY-MM-DD — not maintained; see
   docs/README.md for living docs."*
2. Anything a future operator must act on gets folded into a living doc (or the
   `cei-labs-event` TRACKER) before the session ends.
3. If a dated log is still being consulted regularly, promote it: turn it into
   a living doc and remove the date from its name.
