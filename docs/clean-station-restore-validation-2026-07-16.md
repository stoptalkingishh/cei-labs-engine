# Clean-station restore validation — 2026-07-16

## Scope

A real backup of the `cei-labs` source stack was restored into the empty
`cei-restore-drill` namespace on Docker Desktop Swarm. The rehearsal namespace
had no services and none of the three target volumes before the timed run.
Ports 80/443 were changed to 18080/18443 only in the rehearsal copy so the
source stack could remain online for direct reconciliation. Restored secrets,
TLS configuration, and dynamic Traefik configuration came from the decrypted
protected-config artifact in the scratch deployment root.

Backup run ID: `20260717T015642Z`

## Timing

The Windows desktop command runner detaches at background Docker boundaries,
so the same continuous restore was measured as three adjacent phases:

- verification, protected-config restore, volumes, uploads/orchestrator tar
  restore, and MariaDB launch: 27.7 s
- MariaDB readiness/import validation through stack deploy: 27.8 s
- five-service convergence wait: 13.341 s
- total restore time: **68.841 s (1m 8.841s)**

All five services converged to `1/1`. Exact images and desired replicas for
every service matched `services.json`.

## Reconciliation

| Dataset | Source | Restored | Result |
|---|---:|---:|---|
| Users | 1 | 1 | match |
| Teams | 0 | 0 | match |
| Challenges | 59 | 59 | match |
| Submissions | 0 | 0 | match |
| Aggregate score | 0 | 0 | match |

The backup verification gate passed checksums, manifest format, protected
configuration decryption, both tar archives, and MariaDB dump signature before
any target volume was created.

## Defects found and fixed

1. Tar extraction attempted to set the named-volume root timestamp on Docker
   Desktop. Restore now uses `tar --touch`, preserving restored content while
   avoiding the forbidden root `utime` operation.
2. `docker stack config` emits resolved literal dollar signs, but a later
   `docker stack deploy` parses them as interpolation. Restore now stages the
   recorded YAML with dollars re-escaped before deployment, preserving regex
   anchors and dollar-containing resolved values.
