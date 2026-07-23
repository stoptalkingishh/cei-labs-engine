"""docker/orchestrator/app/store.py

SQLite-backed registries — instances keyed by (owner_id, instance_key),
ranges (shared target-attacker attacker+network) keyed by owner_id alone.

Previously an in-memory dict guarded by a threading.Lock, on the assumption
that the orchestrator only ever runs as a single process. That assumption
was never actually enforced: gunicorn's `--workers` count controls how many
separate OS processes serve this Flask app, each with its own Python heap --
a threading.Lock only ever protects against concurrent *threads inside one
process*, not concurrent *processes*. With `--workers 2`, two independent
in-memory dicts existed, each blind to what the other had done. Verified
live (see TRACKER.md): 2 requests both got `201 created` for the identical
owner_id/instance_key, plus one 500 from a Docker "service name already in
use" collision -- the second worker never saw the first worker's write, so
both proceeded to actually create containers.

SQLite fixes this at the source instead of re-documenting "must stay at
--workers 1": a single shared file every worker process opens, with real
cross-process locking, and a PRIMARY KEY constraint that makes "did someone
already claim this owner_id/instance_key" an atomic, race-free check instead
of a check-then-act two-step. `reserve()` is the load-bearing method -- it's
called *before* any real Docker API call, so only the process that wins the
INSERT ever creates containers; a loser never touches Docker at all.

The stack stores this file on the `orchestrator_data` volume so routine service
restarts keep the authoritative instance/range/port/timer registry aligned
with live Swarm resources. Tests may still use `:memory:` or temporary files.

`plan_json` is the one column that actually carries credential material --
every generated secret (VNC_PASSWORD/OPERATOR_PASSWORD, per-team flag
secrets riding the `access` dict, anything else instance_types.py ever
stuffs into a ServiceSpec's env or a plan's access dict) flows through it.
It is encrypted at rest with AEAD (see crypto.py) before it ever reaches
SQLite -- `_encrypt_plan`/`_decrypt_plan` below are the only two places that
cross the boundary between the plaintext JSON the rest of this file already
worked with and the ciphertext actually written to disk, so the rest of
this module (query shapes, transaction handling, the reservation race logic
et al) is unchanged. `_decrypt_plan` tolerates rows written before this
patch (plain JSON, not a Fernet token) so an in-place upgrade doesn't break
reads of already-running instances; every write after the upgrade always
produces ciphertext.
"""
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from .crypto import CredentialCipher, InvalidToken
from .docker_client import ServiceSpec
from .instance_types import InstancePlan, RangePlan

_SQLITE_INIT_RETRIES = 100
_SQLITE_INIT_RETRY_SECONDS = 0.05


class ReservationCapacityError(Exception):
    def __init__(self, scope: str, limit: int):
        self.scope = scope
        self.limit = limit
        super().__init__(f"{scope} capacity {limit} reached")


@dataclass
class InstanceRecord:
    plan: InstancePlan
    created_at: float
    last_accessed: float = field(default_factory=time.time)
    shutdown_at: "float | None" = None  # set after a correct flag submission
    extensions_used: int = 0

    @property
    def owner_id(self) -> str:
        return self.plan.owner_id

    @property
    def instance_key(self) -> str:
        return self.plan.instance_key

    def touch(self) -> None:
        self.last_accessed = time.time()

    def idle_seconds(self) -> float:
        return time.time() - self.last_accessed

    def shutdown_pending(self) -> bool:
        return self.shutdown_at is not None

    def shutdown_due(self) -> bool:
        return self.shutdown_at is not None and time.time() >= self.shutdown_at


@dataclass
class RangeRecord:
    plan: RangePlan
    created_at: float
    last_accessed: float = field(default_factory=time.time)
    target_keys: set = field(default_factory=set)

    @property
    def owner_id(self) -> str:
        return self.plan.owner_id

    def touch(self) -> None:
        self.last_accessed = time.time()

    def idle_seconds(self) -> float:
        return time.time() - self.last_accessed


def _service_to_dict(svc: ServiceSpec) -> dict:
    return {
        "name": svc.name,
        "image": svc.image,
        "networks": list(svc.networks),
        "labels": dict(svc.labels),
        "env": dict(svc.env),
        "published_ports": [list(p) for p in svc.published_ports],
        "mem_limit_bytes": svc.mem_limit_bytes,
        "mem_reservation_bytes": svc.mem_reservation_bytes,
        "cap_drop": list(svc.cap_drop),
        "cap_add": list(svc.cap_add),
        "read_only": svc.read_only,
        "cpu_limit_nanos": svc.cpu_limit_nanos,
        "sysctls": dict(svc.sysctls),
    }


def _service_from_dict(d: dict) -> ServiceSpec:
    return ServiceSpec(
        name=d["name"],
        image=d["image"],
        networks=d["networks"],
        labels=d.get("labels", {}),
        env=d.get("env", {}),
        published_ports=[tuple(p) for p in d.get("published_ports", [])],
        mem_limit_bytes=d.get("mem_limit_bytes", 512 * 1024 * 1024),
        mem_reservation_bytes=d.get("mem_reservation_bytes", 128 * 1024 * 1024),
        cap_drop=d.get("cap_drop", []),
        cap_add=d.get("cap_add", []),
        read_only=d.get("read_only", False),
        cpu_limit_nanos=d.get("cpu_limit_nanos"),
        sysctls=d.get("sysctls", {}),
    )


def _plan_to_json(plan: InstancePlan) -> str:
    return json.dumps({
        "type": plan.type,
        "owner_id": plan.owner_id,
        "instance_key": plan.instance_key,
        "services": [_service_to_dict(s) for s in plan.services],
        "access": plan.access,
        "network": plan.network,
        "range_owner_id": plan.range_owner_id,
    })


def _plan_from_json(raw: str) -> InstancePlan:
    d = json.loads(raw)
    return InstancePlan(
        type=d["type"],
        owner_id=d["owner_id"],
        instance_key=d["instance_key"],
        services=[_service_from_dict(s) for s in d["services"]],
        access=d["access"],
        network=d.get("network"),
        range_owner_id=d.get("range_owner_id"),
    )


def _range_plan_to_json(plan: RangePlan) -> str:
    return json.dumps({
        "owner_id": plan.owner_id,
        "network": plan.network,
        "attacker_service": _service_to_dict(plan.attacker_service),
        "gateway_service": _service_to_dict(plan.gateway_service) if plan.gateway_service else None,
        "access": plan.access,
    })


def _range_plan_from_json(raw: str) -> RangePlan:
    d = json.loads(raw)
    return RangePlan(
        owner_id=d["owner_id"],
        network=d["network"],
        attacker_service=_service_from_dict(d["attacker_service"]),
        gateway_service=_service_from_dict(d["gateway_service"]) if d.get("gateway_service") else None,
        access=d["access"],
    )


def _encrypt_plan(cipher: CredentialCipher, plan_json: "str | None") -> "str | None":
    """NULL plan_json means "reservation in flight, no plan yet" -- pass
    NULL straight through rather than encrypting the string "null"."""
    if plan_json is None:
        return None
    return cipher.encrypt(plan_json)


def _decrypt_plan(cipher: CredentialCipher, stored: "str | None") -> "str | None":
    """Inverse of _encrypt_plan. Tolerates rows persisted before this module
    started encrypting plan_json: a pre-upgrade row is plain JSON (starts
    with '{'), which is never valid Fernet ciphertext, so InvalidToken (or
    any decode failure) falls back to treating `stored` as already-plaintext
    JSON. This is a one-way migration path -- the next write of that same
    row (finalize/put) always produces ciphertext, so rows self-upgrade as
    they're touched."""
    if stored is None:
        return None
    try:
        return cipher.decrypt(stored)
    except (InvalidToken, ValueError):
        return stored


class InstanceStore:
    def __init__(self, db_path: str = ":memory:", cipher: "CredentialCipher | None" = None):
        self._db_path = db_path
        # See crypto.py: encrypts plan_json (the column carrying every
        # generated credential) before it reaches SQLite. Defaults to an
        # ephemeral key (loud warning logged) if the caller doesn't wire one
        # up -- production (main.py's create_app) always passes a real one.
        self._cipher = cipher or CredentialCipher.from_key_material(None)
        # sqlite3 connections aren't safe to share across threads; each
        # thread (gunicorn sync workers use one thread per request, plus the
        # reaper's own background thread) gets its own connection onto the
        # same on-disk file, which is what actually gives us cross-process
        # (and cross-thread) sharing.
        self._local = threading.local()
        self._open_conns: list[sqlite3.Connection] = []
        self._open_conns_lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False: we still only ever *use* a connection
            # from the thread that opened it (that's what threading.local
            # already guarantees) -- this only relaxes sqlite3's separate
            # restriction on which thread may *close* it, so close() can run
            # from whichever thread happens to tear the store down.
            conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=30000")
            # Gunicorn imports the application independently in every worker.
            # Concurrent workers can request WAL mode at the same instant;
            # SQLite may report "database is locked" for this PRAGMA even
            # with a busy timeout. Retry only that bounded startup contention.
            for attempt in range(_SQLITE_INIT_RETRIES):
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == _SQLITE_INIT_RETRIES - 1:
                        conn.close()
                        raise
                    time.sleep(_SQLITE_INIT_RETRY_SECONDS)
            self._local.conn = conn
            with self._open_conns_lock:
                self._open_conns.append(conn)
        return conn

    def close(self) -> None:
        """Close every thread-local connection this store ever opened.
        Not needed in production (the container just exits), but Windows
        won't let a temp db file be deleted while any connection to it is
        still open -- tests use this for clean teardown."""
        with self._open_conns_lock:
            for conn in self._open_conns:
                conn.close()
            self._open_conns.clear()

    def _init_schema(self) -> None:
        self._conn().execute(
            """
            CREATE TABLE IF NOT EXISTS instances (
                owner_id TEXT NOT NULL,
                instance_key TEXT NOT NULL,
                plan_json TEXT,
                created_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                shutdown_at REAL,
                extensions_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, instance_key)
            )
            """
        )

    def reserve(
        self,
        owner_id: str,
        instance_key: str,
        max_instances: "int | None" = None,
        max_instances_per_owner: "int | None" = None,
    ) -> bool:
        """Atomically claim the right to create this (owner_id, instance_key).
        Returns True iff this call won -- no row existed before. The PRIMARY
        KEY constraint makes this atomic across every worker process sharing
        this db file, closing the exact race verified in TRACKER.md."""
        if max_instances is not None or max_instances_per_owner is not None:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                exists = conn.execute(
                    "SELECT 1 FROM instances WHERE owner_id = ? AND instance_key = ?",
                    (owner_id, instance_key),
                ).fetchone()
                if exists:
                    conn.execute("COMMIT")
                    return False
                if max_instances_per_owner is not None:
                    (owner_count,) = conn.execute(
                        "SELECT COUNT(*) FROM instances WHERE owner_id = ?", (owner_id,)
                    ).fetchone()
                    if owner_count >= max_instances_per_owner:
                        raise ReservationCapacityError("owner", max_instances_per_owner)
                if max_instances is not None:
                    (total_count,) = conn.execute("SELECT COUNT(*) FROM instances").fetchone()
                    if total_count >= max_instances:
                        raise ReservationCapacityError("global", max_instances)
                now = time.time()
                conn.execute(
                    "INSERT INTO instances (owner_id, instance_key, plan_json, created_at, last_accessed, extensions_used) "
                    "VALUES (?, ?, NULL, ?, ?, 0)",
                    (owner_id, instance_key, now, now),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

        now = time.time()
        try:
            self._conn().execute(
                "INSERT INTO instances (owner_id, instance_key, plan_json, created_at, last_accessed, extensions_used) "
                "VALUES (?, ?, NULL, ?, ?, 0)",
                (owner_id, instance_key, now, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def claim_for_replacement(self, owner_id: str, instance_key: str) -> "InstanceRecord | None":
        """Atomically turn a finalized row into an in-flight reservation.

        Relaunch and teardown used to do a separate ``get()`` followed much
        later by an unconditional ``remove()``. Under concurrent relaunches,
        several workers could all read the same plan and a late teardown
        could delete a newer worker's reservation/finalized row. The Docker
        service then remained live while the API reported no instance.

        ``BEGIN IMMEDIATE`` serializes this read-and-transition across every
        gunicorn process sharing the database. Exactly one caller receives
        the old plan and owns the destructive lifecycle operation; everyone
        else sees the NULL ``plan_json`` reservation and waits for that owner
        to finalize or release it.
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT plan_json, created_at, last_accessed, shutdown_at, extensions_used "
                "FROM instances WHERE owner_id = ? AND instance_key = ?",
                (owner_id, instance_key),
            ).fetchone()
            if row is None or row[0] is None:
                conn.execute("COMMIT")
                return None
            plan_json, created_at, last_accessed, shutdown_at, extensions_used = row
            plan_json = _decrypt_plan(self._cipher, plan_json)
            now = time.time()
            conn.execute(
                "UPDATE instances SET plan_json = NULL, created_at = ?, last_accessed = ?, "
                "shutdown_at = NULL, extensions_used = 0 "
                "WHERE owner_id = ? AND instance_key = ?",
                (now, now, owner_id, instance_key),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return InstanceRecord(
            plan=_plan_from_json(plan_json),
            created_at=created_at,
            last_accessed=last_accessed,
            shutdown_at=shutdown_at,
            extensions_used=extensions_used,
        )

    def finalize(self, owner_id: str, instance_key: str, plan: InstancePlan) -> None:
        """Fill in the real plan after a won reservation's Docker resources
        actually got created."""
        self._conn().execute(
            "UPDATE instances SET plan_json = ? WHERE owner_id = ? AND instance_key = ?",
            (_encrypt_plan(self._cipher, _plan_to_json(plan)), owner_id, instance_key),
        )

    def release_reservation(self, owner_id: str, instance_key: str) -> None:
        """Roll back a reservation whose Docker creation failed, so the slot
        doesn't stay stuck forever. Only ever removes a still-pending
        (plan_json IS NULL) row -- never a finalized one."""
        self._conn().execute(
            "DELETE FROM instances WHERE owner_id = ? AND instance_key = ? AND plan_json IS NULL",
            (owner_id, instance_key),
        )

    def put(self, record: InstanceRecord) -> None:
        """One-shot upsert -- used by tests and by the reaper/relaunch paths
        that already know they hold an uncontested slot."""
        self._conn().execute(
            "INSERT INTO instances (owner_id, instance_key, plan_json, created_at, last_accessed, shutdown_at, extensions_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, instance_key) DO UPDATE SET "
            "plan_json=excluded.plan_json, created_at=excluded.created_at, last_accessed=excluded.last_accessed, "
            "shutdown_at=excluded.shutdown_at, extensions_used=excluded.extensions_used",
            (
                record.owner_id,
                record.instance_key,
                _encrypt_plan(self._cipher, _plan_to_json(record.plan)),
                record.created_at,
                record.last_accessed,
                record.shutdown_at,
                record.extensions_used,
            ),
        )

    def get(self, owner_id: str, instance_key: str) -> "InstanceRecord | None":
        row = self._conn().execute(
            "SELECT plan_json, created_at, last_accessed, shutdown_at, extensions_used "
            "FROM instances WHERE owner_id = ? AND instance_key = ?",
            (owner_id, instance_key),
        ).fetchone()
        if row is None or row[0] is None:  # no row, or a reservation still in flight
            return None
        plan_json, created_at, last_accessed, shutdown_at, extensions_used = row
        plan_json = _decrypt_plan(self._cipher, plan_json)
        return InstanceRecord(
            plan=_plan_from_json(plan_json),
            created_at=created_at,
            last_accessed=last_accessed,
            shutdown_at=shutdown_at,
            extensions_used=extensions_used,
        )

    def reservation_pending(self, owner_id: str, instance_key: str) -> bool:
        """True if a reservation row exists but hasn't been finalized yet --
        i.e. another worker is mid-creation right now."""
        row = self._conn().execute(
            "SELECT 1 FROM instances WHERE owner_id = ? AND instance_key = ? AND plan_json IS NULL",
            (owner_id, instance_key),
        ).fetchone()
        return row is not None

    def touch(self, owner_id: str, instance_key: str) -> None:
        self._conn().execute(
            "UPDATE instances SET last_accessed = ? WHERE owner_id = ? AND instance_key = ?",
            (time.time(), owner_id, instance_key),
        )

    def update(self, record: InstanceRecord) -> None:
        """Persist a mutated record back (shutdown_at / extensions_used /
        last_accessed changes) -- call after mutating an object returned by
        get(), since get() always returns a fresh, disconnected copy."""
        self._conn().execute(
            "UPDATE instances SET last_accessed = ?, shutdown_at = ?, extensions_used = ? "
            "WHERE owner_id = ? AND instance_key = ?",
            (record.last_accessed, record.shutdown_at, record.extensions_used, record.owner_id, record.instance_key),
        )

    def remove(self, owner_id: str, instance_key: str) -> None:
        self._conn().execute(
            "DELETE FROM instances WHERE owner_id = ? AND instance_key = ?", (owner_id, instance_key)
        )

    def all(self) -> list:
        rows = self._conn().execute(
            "SELECT plan_json, created_at, last_accessed, shutdown_at, extensions_used "
            "FROM instances WHERE plan_json IS NOT NULL"
        ).fetchall()
        return [
            InstanceRecord(
                plan=_plan_from_json(_decrypt_plan(self._cipher, plan_json)),
                created_at=created_at,
                last_accessed=last_accessed,
                shutdown_at=shutdown_at,
                extensions_used=extensions_used,
            )
            for plan_json, created_at, last_accessed, shutdown_at, extensions_used in rows
        ]

    def count(self) -> int:
        """Counts reservations too (not just finalized instances) -- a
        reservation in flight must count against capacity immediately, or
        concurrent requests could all pass a stale count check before any of
        them finishes creating, overrunning MAX_INSTANCES."""
        (n,) = self._conn().execute("SELECT COUNT(*) FROM instances").fetchone()
        return n

    def count_for_owner(self, owner_id: str) -> int:
        """Counts finalized rows and in-flight reservations for one owner."""
        (n,) = self._conn().execute(
            "SELECT COUNT(*) FROM instances WHERE owner_id = ?", (owner_id,)
        ).fetchone()
        return n

    def pending_count(self) -> int:
        (n,) = self._conn().execute(
            "SELECT COUNT(*) FROM instances WHERE plan_json IS NULL"
        ).fetchone()
        return n

    def release_stale_reservations(self, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        cursor = self._conn().execute(
            "DELETE FROM instances WHERE plan_json IS NULL AND created_at <= ?", (cutoff,)
        )
        return cursor.rowcount


class RangeStore:
    def __init__(self, db_path: str = ":memory:", cipher: "CredentialCipher | None" = None):
        self._db_path = db_path
        self._cipher = cipher or CredentialCipher.from_key_material(None)
        self._local = threading.local()
        self._open_conns: list[sqlite3.Connection] = []
        self._open_conns_lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False: we still only ever *use* a connection
            # from the thread that opened it (that's what threading.local
            # already guarantees) -- this only relaxes sqlite3's separate
            # restriction on which thread may *close* it, so close() can run
            # from whichever thread happens to tear the store down.
            conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
            with self._open_conns_lock:
                self._open_conns.append(conn)
        return conn

    def close(self) -> None:
        """See InstanceStore.close()."""
        with self._open_conns_lock:
            for conn in self._open_conns:
                conn.close()
            self._open_conns.clear()

    def _init_schema(self) -> None:
        self._conn().execute(
            """
            CREATE TABLE IF NOT EXISTS ranges (
                owner_id TEXT PRIMARY KEY,
                plan_json TEXT,
                created_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                target_keys_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )

    def reserve(self, owner_id: str) -> bool:
        """Same pattern as InstanceStore.reserve -- atomically claim the
        right to create this range's shared attacker, across all workers."""
        now = time.time()
        try:
            self._conn().execute(
                "INSERT INTO ranges (owner_id, plan_json, created_at, last_accessed, target_keys_json) "
                "VALUES (?, NULL, ?, ?, '[]')",
                (owner_id, now, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def finalize(self, owner_id: str, plan: RangePlan) -> None:
        self._conn().execute(
            "UPDATE ranges SET plan_json = ? WHERE owner_id = ?",
            (_encrypt_plan(self._cipher, _range_plan_to_json(plan)), owner_id),
        )

    def release_reservation(self, owner_id: str) -> None:
        self._conn().execute("DELETE FROM ranges WHERE owner_id = ? AND plan_json IS NULL", (owner_id,))

    def reservation_pending(self, owner_id: str) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM ranges WHERE owner_id = ? AND plan_json IS NULL", (owner_id,)
        ).fetchone()
        return row is not None

    def pending_count(self) -> int:
        (n,) = self._conn().execute(
            "SELECT COUNT(*) FROM ranges WHERE plan_json IS NULL"
        ).fetchone()
        return n

    def release_stale_reservations(self, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        cursor = self._conn().execute(
            "DELETE FROM ranges WHERE plan_json IS NULL AND created_at <= ?", (cutoff,)
        )
        return cursor.rowcount

    def put(self, record: RangeRecord) -> None:
        self._conn().execute(
            "INSERT INTO ranges (owner_id, plan_json, created_at, last_accessed, target_keys_json) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id) DO UPDATE SET "
            "plan_json=excluded.plan_json, created_at=excluded.created_at, last_accessed=excluded.last_accessed, "
            "target_keys_json=excluded.target_keys_json",
            (
                record.owner_id,
                _encrypt_plan(self._cipher, _range_plan_to_json(record.plan)),
                record.created_at,
                record.last_accessed,
                json.dumps(sorted(record.target_keys)),
            ),
        )

    def get(self, owner_id: str) -> "RangeRecord | None":
        row = self._conn().execute(
            "SELECT plan_json, created_at, last_accessed, target_keys_json FROM ranges WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        plan_json, created_at, last_accessed, target_keys_json = row
        plan_json = _decrypt_plan(self._cipher, plan_json)
        return RangeRecord(
            plan=_range_plan_from_json(plan_json),
            created_at=created_at,
            last_accessed=last_accessed,
            target_keys=set(json.loads(target_keys_json)),
        )

    def touch(self, owner_id: str) -> None:
        self._conn().execute(
            "UPDATE ranges SET last_accessed = ? WHERE owner_id = ?", (time.time(), owner_id)
        )

    def update(self, record: RangeRecord) -> None:
        """Persist mutated target_keys / last_accessed back."""
        self._conn().execute(
            "UPDATE ranges SET last_accessed = ?, target_keys_json = ? WHERE owner_id = ?",
            (record.last_accessed, json.dumps(sorted(record.target_keys)), record.owner_id),
        )

    def remove(self, owner_id: str) -> None:
        self._conn().execute("DELETE FROM ranges WHERE owner_id = ?", (owner_id,))

    def all(self) -> list:
        rows = self._conn().execute(
            "SELECT plan_json, created_at, last_accessed, target_keys_json FROM ranges WHERE plan_json IS NOT NULL"
        ).fetchall()
        return [
            RangeRecord(
                plan=_range_plan_from_json(_decrypt_plan(self._cipher, plan_json)),
                created_at=created_at,
                last_accessed=last_accessed,
                target_keys=set(json.loads(target_keys_json)),
            )
            for plan_json, created_at, last_accessed, target_keys_json in rows
        ]
