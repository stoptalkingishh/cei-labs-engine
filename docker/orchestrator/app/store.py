"""docker/orchestrator/app/store.py

In-memory registry of live instances, keyed by (owner_id, instance_key).

The orchestrator runs as a single replica (it needs exclusive, consistent
access to the Docker socket on one manager node), so an in-process dict
guarded by a lock is sufficient — no external database needed. On restart,
`last_accessed` timestamps reset to "now" for anything still running in
Docker; this only affects idle-reap timing, never correctness of what's
actually deployed.
"""
import threading
import time
from dataclasses import dataclass, field

from .instance_types import InstancePlan


@dataclass
class InstanceRecord:
    plan: InstancePlan
    created_at: float
    last_accessed: float = field(default_factory=time.time)

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


class InstanceStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._instances: dict[tuple[str, str], InstanceRecord] = {}

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

    def all(self) -> list[InstanceRecord]:
        with self._lock:
            return list(self._instances.values())

    def count(self) -> int:
        with self._lock:
            return len(self._instances)
