"""docker/orchestrator/app/reaper.py

Background thread with two independent jobs, both run every sweep:
  1. Idle reaping — tears down instances/ranges nobody has touched in a
     while ("touch" happens on every create_or_get() call, i.e. every time a
     participant (re-)opens the challenge in CTFd — see controller.py).
  2. Shutdown countdowns — tears down an instance whose post-solve
     `shutdown_at` deadline has passed (see controller.schedule_shutdown /
     extend_shutdown), independent of whether it's otherwise "idle".
"""
import logging
import threading

from .controller import InstanceController
from .store import InstanceStore, RangeStore

logger = logging.getLogger(__name__)


class Reaper(threading.Thread):
    def __init__(
        self,
        controller: InstanceController,
        store: InstanceStore,
        range_store: RangeStore,
        grace_minutes: int,
        interval_seconds: int,
    ):
        super().__init__(daemon=True, name="idle-reaper")
        self.controller = controller
        self.store = store
        self.range_store = range_store
        self.grace_seconds = grace_minutes * 60
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def run(self) -> None:
        logger.info("reaper started (grace=%ss, interval=%ss)", self.grace_seconds, self.interval_seconds)
        while not self._stop_event.wait(self.interval_seconds):
            self.sweep()

    def stop(self) -> None:
        self._stop_event.set()

    def sweep(self) -> int:
        reaped = 0
        reaped += self._sweep_due_shutdowns()
        reaped += self._sweep_idle_instances()
        reaped += self._sweep_idle_ranges()
        return reaped

    def _sweep_due_shutdowns(self) -> int:
        reaped = 0
        for record in self.store.all():
            if not record.shutdown_due():
                continue
            logger.info("post-solve shutdown reached for owner=%s key=%s", record.owner_id, record.instance_key)
            try:
                self.controller.teardown(record.owner_id, record.instance_key)
                reaped += 1
            except Exception:
                logger.exception("failed to shut down owner=%s key=%s", record.owner_id, record.instance_key)
        return reaped

    def _sweep_idle_instances(self) -> int:
        reaped = 0
        for record in self.store.all():
            if record.shutdown_pending():
                continue  # already on a countdown, handled above
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

    def _sweep_idle_ranges(self) -> int:
        reaped = 0
        for range_record in self.range_store.all():
            if range_record.idle_seconds() < self.grace_seconds:
                continue
            logger.info("reaping idle range owner=%s idle=%.0fs", range_record.owner_id, range_record.idle_seconds())
            try:
                self.controller.teardown_range(range_record.owner_id)
                reaped += 1
            except Exception:
                logger.exception("failed to reap range owner=%s", range_record.owner_id)
        return reaped
