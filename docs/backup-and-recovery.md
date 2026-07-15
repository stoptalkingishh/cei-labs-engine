# Backup and recovery

CEI Labs treats CTFd data and configuration as durable. Redis is a cache.
Routine orchestrator restarts preserve its SQLite registry in the
`orchestrator_data` volume. A clean-cluster disaster recovery may deliberately
discard active challenge sessions, but must remove their managed Swarm
resources and require participants to relaunch.

## Recovery targets

- CTFd database, uploads, secrets, and approved configuration: provisional
  RPO 15 minutes and RTO 30 minutes, pending a timed rehearsal.
- Active challenge sessions: best effort during a routine restart; explicitly
  non-durable across a clean-cluster rebuild, with relaunch available within
  10 minutes.
- Final scoreboard and evidence: export at checkpoints and event close.

## Create and verify a backup

Create a random passphrase file in the operator's protected credential store,
mode `0600`. Never put it in this repository or in the backup directory.

```bash
export BACKUP_ENCRYPTION_KEY_FILE=/protected/cei-backup.pass
export WARGAMES_REPO=/path/to/CEI-Labs-Wargames
./scripts/backup-platform.sh /protected/backups
./scripts/verify-backup.sh /protected/backups/<UTC-run-id>
```

If the running station's `.env`, secrets, and TLS material live in a separate
deployment checkout, run the script from the exact Engine commit being
recorded and point it at that deployment root:

```bash
DEPLOYMENT_ROOT=/srv/cei-labs-engine-live \
  BACKUP_ENCRYPTION_KEY_FILE=/protected/cei-backup.pass \
  ./scripts/backup-platform.sh /protected/backups
```

The backup briefly scales CTFd and the orchestrator to zero, creates a
transaction-consistent MariaDB dump, archives uploads and orchestrator state,
encrypts `.env`, secrets, and TLS configuration with AES-256-CBC/PBKDF2, then
restores the original replica counts. It records Docker/Swarm metadata,
resolved stack configuration, repository commits, and SHA-256 checksums.

Copy the verified encrypted bundle to a second protected location off the
Docker host. Keep the encryption key separately. A backup existing on only the
station is not a disaster-recovery backup.

## Restore rehearsal gate

Do not restore over a live event. Use a clean station or an explicitly approved
empty Swarm. Before mutation:

1. Run `verify-backup.sh`, including the protected-config decryption check.
2. Record the target host, Docker version, Engine/Wargames commits, and start
   time in UTC.
3. Confirm the target contains no participant data. Require a typed operator
   confirmation before any volume/database replacement.
4. Decrypt `protected-config.tar.enc` into a mode-`0700` staging directory and
   inspect paths before copying them into the Engine checkout.
5. Deploy the recorded immutable image digests, restore `ctfd.sql`,
   `ctfd-uploads.tar`, and `orchestrator-data.tar`, then start the stack.

The rehearsal passes only after automated before/after comparisons cover user,
team, challenge, mapping, submission, solve, score, per-team-secret, and upload
counts/hashes; login and scoreboard rendering work; a restored per-team flag
validates; a relaunch rotates that flag; and the orphan audit finds no stale
services, networks, listeners, or port allocations. Corrupt one copy of a
backup and prove verification stops before mutation. Record measured backup
duration, restore duration, size, RPO, and RTO.

Restore automation is intentionally not marked production-ready until this
clean-station rehearsal has been completed. The verification script is
read-only; it is safe to run against every retained bundle.
