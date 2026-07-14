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

Still ephemeral by design, same as the dict it replaced: a fresh file each
container start is correct, since `last_accessed`/`shutdown_at` are allowed
to reset on restart (only affects idle-reap/countdown timing, never
correctness of what's actually deployed in Docker).
"""
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from .docker_client import ServiceSpec
from .instance_types import InstancePlan, RangePlan


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
        "access": plan.access,
    })


def _range_plan_from_json(raw: str) -> RangePlan:
    d = json.loads(raw)
    return RangePlan(
        owner_id=d["owner_id"],
        network=d["network"],
        attacker_service=_service_from_dict(d["attacker_service"]),
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
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
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

    def reserve(self, owner_id: str, instance_key: str) -> bool:
        """Atomically claim the right to create this (owner_id, instance_key).
        Returns True iff this call won -- no row existed before. The PRIMARY
        KEY constraint makes this atomic across every worker process sharing
        this db file, closing the exact race verified in TRACKER.md."""
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
