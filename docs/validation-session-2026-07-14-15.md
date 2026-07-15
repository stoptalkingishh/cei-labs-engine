# Release-candidate validation session — 2026-07-14/15

This report records the recovery and continuation of the CEI Labs remote
station test after an Internet outage. It is evidence for the exact commits
and local image IDs below, not a blanket approval of later builds.

## Scope and environment

- Station: `192.168.1.98`, single-node Fedora 44 Docker Swarm.
- Engine final source commit: `954243a`.
- Deployed orchestrator source: `f4818a9` plus later documentation/CTFd-only
  commits; local image ID
  `sha256:a99322fd22b8419a2bee010b1eddf0fd15862f3224a16ae3a83eff75ff8f9c18`.
- Trusted TCP gateway image ID:
  `sha256:b39b79158e886cfb1df2cd1413652e25fd9d53c2378a3ab9f22bcb2f96b2469f`.
- Final deployed CTFd image: `cei-labs-ctfd:codex-954243a`, local image ID
  `sha256:04be49bedae7b41e3a1425fbe4b3e6225a405bffe036beefa8a8c7cbd61c352c`.
- Wargames validation commit: `83e9f52`.
- Resource evidence was captured with `scripts/capture-resources.sh`; btop
  remained available in tmux session `cei-monitor` for interactive use.

## Deterministic lifecycle gate

The direct orchestrator harness passed all stages on the real Swarm:

- cold waves at 1, 5, 10, and 20 instances;
- 20 concurrent requests for the same instance;
- 20 parallel relaunch requests;
- zero HTTP 5xx responses and zero non-JSON error responses;
- no remaining challenge services, networks, listeners, or port allocations
  after cleanup.

The complete orchestrator unit suite passed 95 tests. The final
instance-launcher suite passed 19 tests. The last CTFd dialect-compatibility
change was additionally exercised through the live participant API.

## Swarm ingress incident and resolution

The first gateway data-plane run found new gateway tasks stuck in `new`.
Docker daemon logs reported that the default `10.0.0.0/24` ingress network
had no available IPs, while `docker network inspect ingress` showed only two
visible endpoints. Repeated gateway create/delete testing had exhausted or
leaked the allocator's internal reservations.

Host publish mode was tested as a possible way to avoid ingress. A service
without an explicit overlay bound and returned HTTP 200, but the listener
disappeared whenever any explicit overlay was attached. This reproduced the
station's earlier host-mode incompatibility and ruled it out for challenge
gateways.

The accepted design is therefore:

- participant-controlled targets and attackers remain only on dedicated
  `internal: true` overlays and publish no ports;
- only non-root, read-only, capability-free trusted gateways may own
  Traefik labels or routing-mesh ports;
- the station ingress network was rebuilt, during a bounded Traefik
  maintenance window, as the non-overlapping `10.20.0.0/16` pool;
- Traefik ports 80/443 were restored and the service converged.

## Native-Swarm isolation gate

The final live isolation audit passed 42/42 checks:

- web gateway returned HTTP 200;
- both single-target SSH gateways returned real SSH banners;
- range SSH and noVNC gateways were reachable;
- every untrusted app, target, and attacker had exactly one private overlay,
  no published port, and no Traefik routing labels;
- targets could not resolve or reach another tenant or the orchestrator;
- targets and the range attacker could not reach `1.1.1.1:443`;
- a reversible NET_ADMIN host route through the trusted range gateway did
  not cross the gateway;
- every gateway ran as UID 65532 with a read-only root filesystem, zero
  effective capabilities, and IPv4/IPv6 forwarding disabled.

Cleanup then showed no challenge services, challenge networks, or listeners
on the allocated test ports.

## Restart and backup persistence

Four live records — a web app, two single targets, and a target/attacker
range — retained the same access ports across a forced orchestrator service
replacement. They also retained the same records and ports after the
backup procedure scaled CTFd and the orchestrator down and back up.

The first backup attempt correctly restored service replicas after failing
because the candidate source checkout did not contain the station's live
`.env` and secrets directory. Commit `68b015f` added `DEPLOYMENT_ROOT`, so a
tested source checkout can back up a separate deployment checkout without
copying credentials into the candidate tree.

Backup `20260715T030642Z` then passed:

- SHA-256 verification of every retained file;
- AES-256-CBC/PBKDF2 protected-configuration decryption;
- MariaDB dump-format validation;
- CTFd uploads and orchestrator-state archive validation;
- deliberate corrupt-copy rejection before mutation.

An isolated, network-disabled scratch restore subsequently:

- decrypted 9 protected configuration files;
- restored an empty uploads archive and 3 orchestrator-state files;
- imported the MariaDB logical dump;
- queried counts of 26 users, 20 teams, 2 challenges, 89 submissions, and
  24 solves;
- removed the scratch container, volumes, and decrypted staging directory.

This is meaningful restore evidence but is not the required timed full-stack
restore onto a clean station.

## Ten-persona diagnostic

Ten fresh-account persona tasks ran in bounded waves because the execution
environment allowed four simultaneous agents including the coordinator. This
is a ten-persona behavioral diagnostic, not a ten-concurrent load acceptance
test.

Repeated passes covered registration, team creation, challenge discovery,
scoreboard reads, authorization boundaries, owner-selector spoofing,
launcher response redaction, duplicate lifecycle actions, reboot, relaunch,
and participant status polling. No persona retained passwords, cookies,
tokens, flags, generated access values, or response bodies containing
secrets.

The round found:

1. Participant workstations could not resolve `ctfd.ctf.local` without a
   host override.
2. Traefik presented its default self-issued certificate rather than a
   certificate matching the advertised hostname.
3. Only 2 challenges were deployed, so the intended 59-challenge Wargames
   catalog, mappings, progression, and scoring could not be accepted.
4. Unknown, object-valued, missing, and malformed launcher actions were
   initially accepted as create-or-get. Commits `b576fa2` and `dc66a9a`
   added explicit envelope/action validation.
5. The first post-fix valid launch exposed a CTFd 3.8.2 compatibility error:
   `db.session.get_bind()` raised `TypeError` during secret persistence.
   Commit `954243a` uses `db.engine.dialect.name` inside the active app
   context.

The final post-fix persona retest passed all relevant gates:

- malformed JSON: HTTP 400, application failure, no environment;
- missing action: HTTP 400, application failure, no environment;
- object and unknown actions: application failure, no environment;
- status remained HTTP 200 with no owned environment after each rejection;
- explicit `{"action": null}` returned success and a redacted status;
- the final status read returned HTTP 200 with no error;
- no 5xx response occurred.

The round remains a diagnostic failure for release acceptance because DNS,
TLS, full catalog deployment, true ten-way concurrency, visual browser
coverage, and test-account cleanup remain open.

## Wargames validation

Wargames commit `83e9f52` adds pinned CI actions, reproducible base-image
inputs, generated-content validation, release digest enforcement, and
deployment preflight checks. Local validation proved exactly 59 challenge
files, 58 instance mappings, and 3 visible launchers. Release mode accepted
digest-pinned references and rejected floating tags. `deploy.sh` passed Bash
syntax validation. GitHub's Wargames `Validate` workflow passed after push at
repository head `581544f`. The full catalog still requires station deployment.

## Publication and CI follow-up

All four local histories were pushed and their remote branch heads were
compared directly with `git ls-remote`. Engine CI then exposed ShellCheck
warnings in the backup/resource scripts and Hadolint incompatibility with two
Dockerfile heredocs. Engine commits `1ca7c12` and `2fe9338` fixed those findings
with array-safe service inspection, ignored-field placeholders, and portable
`printf`-generated runtime entrypoints that preserve fail-closed password
handling.

The final pushed code/CI head `2fe9338` passed Engine `Validate`, `Build analyst
image`, and `Build Kali noVNC image`. On the preceding documentation head,
`Build CTFd image`, `Build Challenge Instance Orchestrator image`, and `Build
TCP gateway image` also passed. Event and Net define no GitHub Actions
workflows. These post-station CI fixes have lint/build evidence; the exact
live-station acceptance evidence remains scoped to the source and image IDs
listed at the start of this report.

## Monitoring result

The Engine monitoring foundation is intentionally two-part:

- `btop` in `tmux` is the interactive operator view and exposes no network
  service;
- `scripts/capture-resources.sh` is the durable timestamped evidence source
  for host, network, container, service, and Docker-event telemetry.

The common Ansible role installs btop through `apt` on Debian/Ubuntu nodes.
The Fedora test station used a user-local btop installation, so Fedora-native
package automation remains an installation portability gap, not a monitoring
functionality gap.

## Remaining release gates

- Deploy and validate all 59 Wargames challenges and 58 mappings.
- Run a genuine ten-concurrent participant acceptance test.
- Run 40-user, burst, and event-duration soak tests.
- Configure participant DNS and a hostname-valid TLS certificate.
- Perform a timed full-stack restore on clean infrastructure.
- Clean up disposable persona accounts and teams through an approved admin
  workflow.
- Pin/freeze the final release set after the remaining acceptance gates pass.
