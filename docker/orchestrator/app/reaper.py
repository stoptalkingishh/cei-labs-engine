"""docker/orchestrator/app/reaper.py

Background thread that tears down instances nobody has touched in a while.
"Touch" happens on every create_or_get() call, i.e. every time a participant
(re-)opens the challenge in CTFd — see controller.py.
"""
import logging
import threading

from .controller import InstanceController
from .store import InstanceStore

logger = logging.getLogger(__name__)


class Reaper(threading.Thread):
    def __init__(self, controller: InstanceController, store: InstanceStore, grace_minutes: int, interval_seconds: int):
        super().__init__(daemon=True, name="idle-reaper")
        self.controller = controller
        self.store = store
        self.grace_seconds = grace_minutes * 60
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def run(self) -> None:
        logger.info("idle reaper started (grace=%ss, interval=%ss)", self.grace_seconds, self.interval_seconds)
        while not self._stop_event.wait(self.interval_seconds):
            self.sweep()

    def stop(self) -> None:
        self._stop_event.set()

    def sweep(self) -> int:
        reaped = 0
        for record in self.store.all():
            if record.idle_seconds() < self.grace_seconds:
                continue
            logger.info(
                "reaping idle instance owner=%s key=%s idle=%.0fs",
                record.owner_id, record.instance_key, record.idle_seconds(),
            )
            try:
                self.controller.teardown(record.owner_id, record.instance_key)
                reaped += 1
            except Exception:
                logger.exception("failed to reap owner=%s key=%s", record.owner_id, record.instance_key)
        return reaped
