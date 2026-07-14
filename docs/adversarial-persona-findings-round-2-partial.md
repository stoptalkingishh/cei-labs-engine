# Adversarial Persona Round 2 — Interrupted Run and Concurrency Investigation

**Run date:** 2026-07-13  
**Investigation/update date:** 2026-07-14  
**Deployment tested:** `192.168.1.98`, Docker Swarm, orchestrator image `cei-labs-orchestrator:race-fix`  
**Engine deployment commit reported by the handoff:** `2fe010e`  
**Local handoff commit:** `e7a723b`  
**Verdict:** **FAIL / INCOMPLETE — not a 10-persona acceptance pass**

## Executive summary

Round 2 started ten concurrent personas—two instances of each of the five
documented persona types. Four completed and six were terminated by the AI
provider's session/API limit. Every completed persona independently
reproduced launch/relaunch failures under real shared load. The four reports
are retained under `docs/adversarial-persona-round2-findings/`.

The evidence proves the release candidate was not concurrency-safe:

- relaunch success ranged from 0% in true parallel bursts to approximately
  31% in less aggressive sequences;
- first-time cold launches also returned the same generic orchestrator 500;
- CTFd sometimes reported `status: null` while an old SSH port/container
  remained reachable;
- two old/new containers could remain reachable for the same logical
  instance, with different hostnames and flags;
- one request received a raw Flask HTML error response rather than the
  plugin's normal JSON error shape;
- reboot-in-place remained reliable, isolating the worst behavior to the
  teardown/create and shared-create paths.

This interrupted run is valuable regression evidence, but it cannot satisfy
the planned 10-persona acceptance gate. After the fix is deployed, all ten
personas must run fresh against one immutable build; the four pre-fix reports
must not be combined with six post-fix reports and called one pass.

## Completion accounting

| Persona | Result | Key evidence |
|---|---|---|
| Baseline `alex_a` | Complete | 5/16 relaunch success; transient null state; eventual recovery |
| Baseline `alex_b` | Complete | 18/23 creation-triggering calls failed; cold create failed three times |
| Power user `jordan_a` | Complete | 0/20 parallel relaunch success; persistent old and new SSH containers |
| Power user `jordan_b` | Complete | 0% parallel success; degraded sequential success; orphan; raw HTML error |
| Novice `sam_a`, `sam_b` | Incomplete | Provider session limit before final report |
| Cheater `riley_a`, `riley_b` | Incomplete | Provider session limit before final report |
| Breaker `morgan_a`, `morgan_b` | Incomplete | Provider session limit before final report |

## Evidence availability and limitation

Claude attempted `docker service logs cei-labs_orchestrator --since 40m`
immediately after the run, filtering for `Traceback`; that command returned
no output. The session was then stopped at the user's request. On 2026-07-14,
Codex attempted a new read-only SSH inspection, but the approval layer
rejected elevated key access before the command ran. No current station log,
health, or deployment command was executed, and no remote state was changed.

Consequently, the mechanisms below are derived from directly inspected code
and matched to the observed symptoms. The code defects are real and locally
regression-tested, but live Swarm verification remains required.

## Root cause 1 — relaunch lifecycle was not atomically owned

### Previous behavior

`InstanceController.create_or_get(..., force_relaunch=True)` performed:

1. `store.get(owner_id, instance_key)`;
2. `teardown()`, including Docker/network cleanup;
3. unconditional `store.remove(owner_id, instance_key)`;
4. a new `store.reserve()` and create/finalize sequence.

Concurrent workers could all read the same finalized record before any one
removed it. A late teardown could then delete another worker's newer pending
reservation or finalized row. That leaves Docker resources alive while the
authoritative API store reports no instance—the exact `status: null` plus
reachable-SSH symptom observed by both power-user reports.

### Local fix

`InstanceStore.claim_for_replacement()` now uses a SQLite
`BEGIN IMMEDIATE` transaction to atomically transition one finalized row to
an in-flight reservation (`plan_json = NULL`) while returning the old plan to
exactly one caller. Only that caller may remove Docker resources. Concurrent
callers wait for the owner to finalize or release the reservation.

Teardown now removes resources from an already-claimed record and releases
only the pending row it owns. It no longer performs a late unconditional
delete that can erase a newer lifecycle operation.

## Root cause 2 — published-port allocation was process-local

The original shared-store fix made instance reservation safe across Gunicorn
workers, but `PortAllocator` remained a Python set guarded by
`threading.Lock`. Production runs two Gunicorn worker processes, so each had
an independent set and could allocate the same port to unrelated cold
creates. Swarm would reject one of those published-port/service operations,
explaining why round 2 expanded from relaunch-only failures to first-time
cold-launch failures during shared load.

`PortAllocator` now accepts the orchestrator SQLite path and atomically
claims ports through an `allocated_ports(port PRIMARY KEY)` table. The
in-memory implementation remains available for isolated unit tests.

## Drain timeout status

The earlier relaunch fix waits up to 15 seconds for removed Swarm tasks to
reach a terminal state, then logs a warning and proceeds. Slower draining
under load may still cause Docker-level name/network conflicts, but the
preserved logs do not prove this. The timeout was not increased in this
change. Live telemetry must measure actual drain duration before deciding
whether to return a retryable 503, extend the bound, or add a stronger Docker
resource-existence gate.

## Root cause 3 — dynamic-secret persistence used a racy upsert

The raw Flask HTML response has a direct concurrency match in the CTFd
plugin. `_persist_and_scrub_secrets()` queried the unique
`(owner_id, challenge_id)` row and inserted when absent. Parallel launch
responses can both observe no row; one insert then loses the unique-key race
with an uncaught database integrity error, escaping Flask's JSON route.

The plugin now uses database-native upserts for production MariaDB and local
SQLite. Status finalization also has a rollback/error boundary that logs the
exception and returns a generic JSON-safe retry message instead of a raw HTML
error page. This mechanism is code-confirmed but, like the two orchestrator
fixes, still requires a live parallel verification against the station.

## Local verification completed

Tests were run from the isolated task virtual environment with third-party
pytest auto-loading disabled.

- Focused controller/store/port suite: **30 passed**.
- New concurrency subset: **11 passed**.
- New 20-worker relaunch-claim test: exactly one worker owns replacement.
- New 20-worker port test: all 20 workers receive distinct ports.
- New controller-level 20-way parallel relaunch: exactly one replacement is
  created, one final instance remains tracked, and every caller completes.
- Dynamic-secret persistence now uses an atomic database upsert and has a
  JSON-safe rollback boundary; the plugin suite remains green, but a full
  CTFd/MariaDB parallel integration test is still required.
- `git diff --check`: clean.

The previous pickup run reached all 88 orchestrator and all 17 plugin
assertions, but those earlier pytest processes hung during teardown. Focused
runs from the orchestrator working directory now exit cleanly. A complete
suite rerun is still required before deployment.

## Resource monitoring added locally

- Ansible's common Ubuntu/Debian package set now includes `btop`.
- `scripts/status.sh` reports whether `btop` is present and shows the safe
  `tmux new -As cei-monitor btop` operator command.
- `scripts/capture-resources.sh` records timestamped host, network,
  container, service, and Docker-event evidence into a run-specific
  directory.
- The README documents that `btop` is a live TUI, not historical evidence;
  the collector is the durable correlation source.
- No monitoring port, daemon, privileged container, or new firewall rule is
  introduced. Existing Glances, if used, should remain loopback-only behind
  SSH forwarding.

## Required live verification before closing the defect

1. Obtain read-only SSH access and capture the current station baseline.
2. Pull/build the candidate without overwriting unrelated remote changes.
3. Run the full unit/plugin suites and record clean process exit codes.
4. Deploy the candidate orchestrator image and record its immutable image ID.
5. Start `btop` in tmux and `capture-resources.sh` with a unique run ID.
6. Run deterministic cold-create and relaunch stages at 1, 5, 10, and 20
   concurrency, then a bounded burst.
7. After each stage, compare the SQLite admin view with Docker services,
   tasks, containers, networks, and published ports. There must be no orphan,
   duplicate, or untracked resource.
8. Confirm every expected contention response is structured JSON and uses a
   retryable status; no raw Flask page may escape.
9. Export orchestrator and Docker daemon logs with UTC timestamps.
10. Only after the deterministic gate passes, run a fresh 10/10 persona
    acceptance round on the same commit/image.

## Still-open independent risks

- Routing-mesh publishing still breaks the intended network airgap: internet
  egress and cross-instance reachability remain open from round 1.
- The 15-second Swarm drain bound is not yet characterized under load.
- There is no launch/relaunch admission control or queue; overload behavior
  must be made explicit and participant-friendly.
- Venue router, VLAN, DHCP/DNS, firewall, and AP telemetry are not covered by
  a single-station test.
