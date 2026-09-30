#!/usr/bin/env python3
"""Seed the orchestrator secret vaults from already-running instances.

Run this ONCE on a station being upgraded to an orchestrator build that has
the secret vault (see docs/credential-lifecycle.md), while the pre-upgrade
instances are still up.

Why it is needed: controller._vault_track_secrets() only ever runs on a
*create*. Deploying the vault does nothing for environments that already
exist -- they stay unvaulted until their next teardown, which is precisely
the moment their secrets are destroyed. There is nothing left to restore by
then. This closes that window by seeding the vault from state the running
instances still carry.

Where the values come from: each instance's persisted plan_json still holds
the exact LEVEL_SECRETS blob its target container was handed at creation, and
each range's plan holds the attacker ssh_password/novnc_password. Both are
decryptable with the station's own credential_encryption_key, so a team's
current secrets can be recovered without touching the containers.

Run it inside the orchestrator container, which already has /app on the path
and the secret mounted:

    docker cp scripts/backfill-secret-vault.py \\
      $(docker ps -q -f name=cei-labs_orchestrator):/tmp/
    docker exec $(docker ps -q -f name=cei-labs_orchestrator) \\
      python3 /tmp/backfill-secret-vault.py            # dry run
    docker exec $(docker ps -q -f name=cei-labs_orchestrator) \\
      python3 /tmp/backfill-secret-vault.py --commit

Safe to run against a live station: it only CREATEs the vault tables (with
IF NOT EXISTS, exactly as InstanceStore._init_schema does) and INSERTs into
them. It never touches `instances`, `ranges`, or any column the running
process reads or writes. Idempotent -- re-running is a no-op once converged.

Ordering note: run this BEFORE rolling out the vault image if you can. Any
instance created in the gap between backfill and rollout will not be
protected, and will rotate on its next teardown.
"""
import argparse
import json
import sqlite3
import sys
import time

sys.path.insert(0, "/app")

from app.config import Config
from app.crypto import CredentialCipher

# The shared range attacker's credentials, which live in range_secret_vault
# keyed by owner alone -- not in the per-instance vault.
RANGE_CREDENTIAL_KEYS = ("ssh_password", "novnc_password")


def level_secrets_of(plan: dict) -> dict:
    """The exact LEVEL_SECRETS blob the running target container was given.

    Read from the plan's own service env rather than filtering the `access`
    dict: access carries connect info (connect_host/port/note/protocol) and,
    for target-attacker plans, eight more non-level keys (attacker_url,
    ssh_password, novnc_password, ...). Any prefix- or exclusion-based guess
    at "which of these are level secrets" gets target-attacker plans wrong.
    LEVEL_SECRETS is authoritative and needs no per-track knowledge.
    """
    for service in plan.get("services") or []:
        blob = (service.get("env") or {}).get("LEVEL_SECRETS")
        if blob:
            return json.loads(blob)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="actually write; default is a dry run")
    parser.add_argument("--db", default=Config.STORE_DB_PATH,
                        help="orchestrator state db (default: %(default)s)")
    args = parser.parse_args()

    cipher = CredentialCipher.from_key_material(Config.CREDENTIAL_ENCRYPTION_KEY)
    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS instance_secret_vault (
            owner_id TEXT NOT NULL,
            instance_key TEXT NOT NULL,
            secrets_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (owner_id, instance_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS range_secret_vault (
            owner_id TEXT PRIMARY KEY,
            secrets_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    verb = "WRITE" if args.commit else "DRY  "
    instances_done = ranges_done = secrets_total = 0

    print("== instance_secret_vault (per-team level flags) ==")
    for owner_id, instance_key, plan_json in conn.execute(
        "SELECT owner_id, instance_key, plan_json FROM instances WHERE plan_json IS NOT NULL"
    ).fetchall():
        truth = level_secrets_of(json.loads(cipher.decrypt(plan_json)))
        if not truth:
            print(f"  SKIP  {owner_id:>5} {instance_key:<16} no LEVEL_SECRETS in plan")
            continue

        row = conn.execute(
            "SELECT secrets_json FROM instance_secret_vault WHERE owner_id = ? AND instance_key = ?",
            (owner_id, instance_key),
        ).fetchone()
        existing = json.loads(cipher.decrypt(row[0])) if row else {}

        # The running container is the source of truth. A vaulted value that
        # disagrees means the instance was recreated after it was vaulted, so
        # CTFd's instance_launcher_team_secrets was updated to the newer value
        # too -- restoring the older one would leave the box serving passwords
        # CTFd no longer accepts.
        drifted = sorted(k for k in truth if k in existing and existing[k] != truth[k])
        stale = sorted(set(existing) - set(truth))

        note = f"{len(truth):>3} level keys"
        if stale:
            note += f", dropping {len(stale)} non-level"
        if drifted:
            note += f", {len(drifted)} stale value(s) replaced from live plan"
        print(f"  {verb} {owner_id:>5} {instance_key:<16} {note}")

        secrets_total += len(truth)
        instances_done += 1
        if args.commit:
            conn.execute(
                "INSERT INTO instance_secret_vault (owner_id, instance_key, secrets_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(owner_id, instance_key) DO UPDATE SET "
                "secrets_json=excluded.secrets_json, updated_at=excluded.updated_at",
                (owner_id, instance_key, cipher.encrypt(json.dumps(truth)), time.time()),
            )

    print()
    print("== range_secret_vault (shared attacker credentials) ==")
    for owner_id, plan_json in conn.execute(
        "SELECT owner_id, plan_json FROM ranges WHERE plan_json IS NOT NULL"
    ).fetchall():
        access = json.loads(cipher.decrypt(plan_json)).get("access") or {}
        creds = {k: access[k] for k in RANGE_CREDENTIAL_KEYS if k in access}
        if not creds:
            print(f"  SKIP  {owner_id:>5} no attacker credentials in plan")
            continue

        row = conn.execute(
            "SELECT secrets_json FROM range_secret_vault WHERE owner_id = ?", (owner_id,)
        ).fetchone()
        existing = json.loads(cipher.decrypt(row[0])) if row else {}
        merged = {**creds, **existing}  # existing wins, as merge_vaulted_secrets does

        print(f"  {verb} {owner_id:>5} {len(merged)} creds "
              f"({len(merged) - len(existing)} new, {len(existing)} already vaulted)")
        ranges_done += 1
        if args.commit:
            conn.execute(
                "INSERT INTO range_secret_vault (owner_id, secrets_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(owner_id) DO UPDATE SET "
                "secrets_json=excluded.secrets_json, updated_at=excluded.updated_at",
                (owner_id, cipher.encrypt(json.dumps(merged)), time.time()),
            )

    if args.commit:
        conn.commit()

    print()
    print(f"instances vaulted : {instances_done}")
    print(f"ranges vaulted    : {ranges_done}")
    print(f"secrets covered   : {secrets_total}")
    if not args.commit:
        print("\nDRY RUN -- nothing written. Re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
