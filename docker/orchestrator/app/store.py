"""docker/orchestrator/app/store.py

In-memory registries — instances keyed by (owner_id, instance_key), ranges
(shared target-attacker attacker+network) keyed by owner_id alone.

The orchestrator runs as a single replica (it needs exclusive, consistent
access to the Docker socket on one manager node), so in-process dicts guarded
by a lock are sufficient — no external database needed. On restart,
`last_accessed`/`shutdown_at` reset for anything still running in Docker;
this only affects idle-reap/countdown timing, never correctness of what's
actually deployed.
"""
import threading
import time
from dataclasses import dataclass, field

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


class InstanceStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._instances: "dict[tuple[str, str], InstanceRecord]" = {}

    def put(self, record: InstanceRecord) -> None:
        with self._lock:
            self._instances[(record.owner_id, record.instance_key)] = record

    def get(self, owner_id: str, instance_key: str) -> "InstanceRecord | None":
        with self._lock:
            return self._instances.get((owner_id, instance_key))

    def touch(self, owner_id: str, instance_key: str) -> None:
        with self._lock:
            record = self._instances.get((owner_id, instance_key))
            if record:
                record.touch()

    def remove(self, owner_id: str, instance_key: str) -> None:
        with self._lock:
            self._instances.pop((owner_id, instance_key), None)

    def all(self) -> list:
        with self._lock:
            return list(self._instances.values())

    def count(self) -> int:
        with self._lock:
            return len(self._instances)


class RangeStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._ranges: "dict[str, RangeRecord]" = {}

    def put(self, record: RangeRecord) -> None:
        with self._lock:
            self._ranges[record.owner_id] = record

    def get(self, owner_id: str) -> "RangeRecord | None":
        with self._lock:
            return self._ranges.get(owner_id)

    def touch(self, owner_id: str) -> None:
        with self._lock:
            record = self._ranges.get(owner_id)
            if record:
                record.touch()

    def remove(self, owner_id: str) -> None:
        with self._lock:
            self._ranges.pop(owner_id, None)

    def all(self) -> list:
        with self._lock:
            return list(self._ranges.values())
