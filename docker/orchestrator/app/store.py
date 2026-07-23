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
"""
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field

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


class InstanceStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
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
            (_plan_to_json(plan), owner_id, instance_key),
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
                _plan_to_json(record.plan),
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
                plan=_plan_from_json(plan_json),
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
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
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
            "UPDATE ranges SET plan_json = ? WHERE owner_id = ?", (_range_plan_to_json(plan), owner_id)
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
                _range_plan_to_json(record.plan),
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
                plan=_range_plan_from_json(plan_json),
                created_at=created_at,
                last_accessed=last_accessed,
                target_keys=set(json.loads(target_keys_json)),
            )
            for plan_json, created_at, last_accessed, target_keys_json in rows
        ]


class WalletStore:
    """SQLite-backed hint-wallet state: the accepted three-track catalog
    (`wallet_catalog`, a singleton row -- see the revision/digest contract in
    docs/P0-FIX-LOG-2026-07-23.md), per-team credit balances
    (`team_balance`), and a record of which (owner, track, entry, tier)
    hints have already been unlocked (`wallet_unlocks`), so a retried
    /wallet/deduct call is idempotent instead of double-charging.

    Same cross-process-safety rationale as InstanceStore/RangeStore above:
    gunicorn workers are separate processes, so state that must never race
    (accepting a catalog, spending a balance) is done inside a `BEGIN
    IMMEDIATE` transaction on the one shared SQLite file rather than in
    Python-level locking that only covers one process.
    """

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._local = threading.local()
        self._open_conns: list[sqlite3.Connection] = []
        self._open_conns_lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=30000")
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
        with self._open_conns_lock:
            for conn in self._open_conns:
                conn.close()
            self._open_conns.clear()

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_catalog (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                revision INTEGER NOT NULL,
                digest TEXT NOT NULL,
                bundle_json TEXT NOT NULL,
                secret_id TEXT NOT NULL,
                accepted_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS team_balance (
                owner_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_unlocks (
                owner_id TEXT NOT NULL,
                track TEXT NOT NULL,
                entry_name TEXT NOT NULL,
                tier INTEGER NOT NULL,
                cost INTEGER NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (owner_id, track, entry_name, tier)
            )
            """
        )

    # ── Catalog sync ─────────────────────────────────────────────────────
    def get_catalog(self) -> "dict | None":
        row = self._conn().execute(
            "SELECT revision, digest, bundle_json, secret_id, accepted_at FROM wallet_catalog WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        revision, digest, bundle_json, secret_id, accepted_at = row
        return {
            "revision": revision,
            "digest": digest,
            "manifests": json.loads(bundle_json),
            "secret_id": secret_id,
            "accepted_at": accepted_at,
        }

    def try_accept_catalog(self, revision: int, digest: str, manifests: list, secret_id: str) -> str:
        """Atomically apply the revision/digest state machine documented in
        docs/P0-FIX-LOG-2026-07-23.md:

          - no catalog yet, or revision strictly greater than stored -> the
            new catalog is committed; returns "accepted".
          - revision equal to stored and digest equal -> idempotent retry,
            no write; returns "idempotent".
          - revision equal to stored and digest different -> returns
            "conflict" (caller maps to 409 revision_digest_conflict).
          - revision less than stored -> returns "stale" (caller maps to
            409 stale_revision).

        The whole check-then-write is inside one BEGIN IMMEDIATE transaction
        so two concurrent /wallet/sync calls (e.g. a retried deploy racing
        the original) can't both observe "no catalog yet" and both try to
        insert -- exactly the same race class InstanceStore.reserve() closes
        for instance creation.
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision, digest FROM wallet_catalog WHERE id = 1").fetchone()
            if row is not None:
                current_revision, current_digest = row
                if revision < current_revision:
                    conn.execute("COMMIT")
                    return "stale"
                if revision == current_revision:
                    conn.execute("COMMIT")
                    return "idempotent" if digest == current_digest else "conflict"
            conn.execute(
                "INSERT INTO wallet_catalog (id, revision, digest, bundle_json, secret_id, accepted_at) "
                "VALUES (1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, digest=excluded.digest, "
                "bundle_json=excluded.bundle_json, secret_id=excluded.secret_id, accepted_at=excluded.accepted_at",
                (revision, digest, json.dumps(manifests), secret_id, time.time()),
            )
            conn.execute("COMMIT")
            return "accepted"
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    # ── Balances ─────────────────────────────────────────────────────────
    def get_balance(self, owner_id: str) -> int:
        row = self._conn().execute(
            "SELECT balance FROM team_balance WHERE owner_id = ?", (owner_id,)
        ).fetchone()
        return row[0] if row is not None else 0

    def credit(self, owner_id: str, amount: int) -> int:
        """Atomically add `amount` (must be > 0) to a team's balance,
        creating the row on first credit. Returns the new balance."""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO team_balance (owner_id, balance) VALUES (?, ?) "
                "ON CONFLICT(owner_id) DO UPDATE SET balance = balance + excluded.balance",
                (owner_id, amount),
            )
            (new_balance,) = conn.execute(
                "SELECT balance FROM team_balance WHERE owner_id = ?", (owner_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return new_balance
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def unlock_hint(self, owner_id: str, track: str, entry_name: str, tier: int, cost: int):
        """Atomically spend `cost` against `owner_id`'s balance to unlock one
        hint tier, unless it was already unlocked (idempotent retry -- a
        plugin request that timed out client-side after server-side success
        must not double-charge on retry).

        Returns (status, balance) where status is one of "unlocked",
        "already_unlocked", "insufficient_balance". The whole
        check-balance-then-spend sequence runs inside one BEGIN IMMEDIATE
        transaction so concurrent deduct calls for the same team can't both
        read the same starting balance and both succeed past a check that
        should only let one of them through -- the double-spend race this
        table exists to close.
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            already = conn.execute(
                "SELECT cost FROM wallet_unlocks WHERE owner_id = ? AND track = ? AND entry_name = ? AND tier = ?",
                (owner_id, track, entry_name, tier),
            ).fetchone()
            if already is not None:
                conn.execute("COMMIT")
                return "already_unlocked", self.get_balance(owner_id)
            row = conn.execute(
                "SELECT balance FROM team_balance WHERE owner_id = ?", (owner_id,)
            ).fetchone()
            balance = row[0] if row is not None else 0
            if balance < cost:
                conn.execute("COMMIT")
                return "insufficient_balance", balance
            new_balance = balance - cost
            conn.execute(
                "INSERT INTO team_balance (owner_id, balance) VALUES (?, ?) "
                "ON CONFLICT(owner_id) DO UPDATE SET balance = excluded.balance",
                (owner_id, new_balance),
            )
            conn.execute(
                "INSERT INTO wallet_unlocks (owner_id, track, entry_name, tier, cost, unlocked_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (owner_id, track, entry_name, tier, cost, time.time()),
            )
            conn.execute("COMMIT")
            return "unlocked", new_balance
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
