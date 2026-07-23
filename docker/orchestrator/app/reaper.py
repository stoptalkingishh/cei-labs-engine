"""docker/orchestrator/app/reaper.py

Background thread with four independent jobs, all run every sweep:
  1. Idle pausing — non-destructively stops instances/ranges nobody has
     touched in a while ("touch" happens on every create_or_get() call, i.e.
     every time a participant (re-)opens the challenge in CTFd — see
     controller.py). Credentials/flags are preserved (controller.pause()) so
     the next touch resumes the SAME environment instead of a fresh one.
  2. Shutdown countdowns — non-destructively pauses an instance whose
     post-solve `shutdown_at` deadline has passed (see
     controller.schedule_shutdown/extend_shutdown), independent of whether
     it's otherwise "idle". Same credential-preserving pause as #1.
  3. Absolute lifetime enforcement: active touches cannot keep participant
     resources alive beyond the configured session ceiling. This IS
     destructive (controller.teardown()/teardown_range()) -- it's a genuine
     expiration, not a pause, and does end the credentials' validity; see
     docs/credential-lifecycle.md for what's exposed to warn callers before
     this fires.
  4. Crash/orphan recovery: releases stale creation reservations and
     reconciles label-managed Docker services/networks against the stores.

Only #3 above, and an explicit relaunch/reset request
(InstanceController.create_or_get(force_relaunch=True)) or delete
(teardown()/teardown_range()), ever end with different credentials than the
environment already had. #1 and #2 are pause/resume — see controller.py's
"Pause / resume" section for the mechanics.
"""
import logging
import threading
import time

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
        max_lifetime_minutes: "int | None" = None,
        reservation_timeout_seconds: int = 300,
    ):
        super().__init__(daemon=True, name="idle-reaper")
        self.controller = controller
        self.store = store
        self.range_store = range_store
        self.grace_seconds = grace_minutes * 60
        self.max_lifetime_seconds = max_lifetime_minutes * 60 if max_lifetime_minutes else None
        self.reservation_timeout_seconds = reservation_timeout_seconds
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def run(self) -> None:
        logger.info(
            "reaper started (grace=%ss, lifetime=%ss, interval=%ss)",
            self.grace_seconds,
            self.max_lifetime_seconds,
            self.interval_seconds,
        )
        while not self._stop_event.wait(self.interval_seconds):
            self.sweep()

    def stop(self) -> None:
        self._stop_event.set()

    def sweep(self) -> int:
        reaped = 0
        reaped += self._release_stale_reservations()
        reaped += self._sweep_due_shutdowns()
        reaped += self._sweep_expired_instances()
        reaped += self._sweep_idle_instances()
        reaped += self._sweep_expired_ranges()
        reaped += self._sweep_idle_ranges()
        reaped += self._sweep_orphaned_resources()
        return reaped

    def _release_stale_reservations(self) -> int:
        released = self.store.release_stale_reservations(self.reservation_timeout_seconds)
        released += self.range_store.release_stale_reservations(self.reservation_timeout_seconds)
        if released:
            logger.warning("released %s stale creation reservation(s)", released)
        return released

    def _sweep_expired_instances(self) -> int:
        if self.max_lifetime_seconds is None:
            return 0
        reaped = 0
        for record in self.store.all():
            if time.time() - record.created_at < self.max_lifetime_seconds:
                continue
            logger.info("reaping expired instance owner=%s key=%s", record.owner_id, record.instance_key)
            try:
                if self.controller.teardown(record.owner_id, record.instance_key):
                    reaped += 1
            except Exception:
                logger.exception("failed to reap expired owner=%s key=%s", record.owner_id, record.instance_key)
        return reaped

    def _sweep_expired_ranges(self) -> int:
        if self.max_lifetime_seconds is None:
            return 0
        reaped = 0
        for record in self.range_store.all():
            if time.time() - record.created_at < self.max_lifetime_seconds:
                continue
            logger.info("reaping expired range owner=%s", record.owner_id)
            try:
                if self.controller.teardown_range(record.owner_id):
                    reaped += 1
            except Exception:
                logger.exception("failed to reap expired range owner=%s", record.owner_id)
        return reaped

    def _sweep_due_shutdowns(self) -> int:
        # Non-destructive: a post-solve countdown reaching zero is automatic
        # housekeeping, not an explicit reset/relaunch request, so this must
        # not rotate the instance's credentials/flags. controller.pause()
        # stops the container(s) but keeps the store row (and therefore the
        # plan/env the credentials live in) so a later create_or_get()
        # resumes with the SAME values instead of generating new ones. See
        # controller.py's "Pause / resume" section docstring.
        reaped = 0
        for record in self.store.all():
            if record.stopped or not record.shutdown_due():
                continue
            logger.info("post-solve shutdown reached for owner=%s key=%s", record.owner_id, record.instance_key)
            try:
                self.controller.pause(record.owner_id, record.instance_key)
                reaped += 1
            except Exception:
                logger.exception("failed to shut down owner=%s key=%s", record.owner_id, record.instance_key)
        return reaped

    def _sweep_idle_instances(self) -> int:
        # Non-destructive, same reasoning as _sweep_due_shutdowns: idle is an
        # automatic pause, not a reset, so credentials/flags must survive it.
        reaped = 0
        for record in self.store.all():
            if record.stopped:
                continue  # already paused
            if record.shutdown_pending():
                continue  # already on a countdown, handled above
            if record.idle_seconds() < self.grace_seconds:
                continue
            logger.info(
                "pausing idle instance owner=%s key=%s idle=%.0fs",
                record.owner_id, record.instance_key, record.idle_seconds(),
            )
            try:
                self.controller.pause(record.owner_id, record.instance_key)
                reaped += 1
            except Exception:
                logger.exception("failed to pause owner=%s key=%s", record.owner_id, record.instance_key)
        return reaped

    def _sweep_idle_ranges(self) -> int:
        # Non-destructive -- see _sweep_idle_instances. Individual range
        # targets are paused independently via _sweep_idle_instances above
        # (they're ordinary rows in the same instance store); this only
        # pauses the shared attacker+gateway.
        reaped = 0
        for range_record in self.range_store.all():
            if range_record.stopped:
                continue  # already paused
            if range_record.idle_seconds() < self.grace_seconds:
                continue
            logger.info("pausing idle range owner=%s idle=%.0fs", range_record.owner_id, range_record.idle_seconds())
            try:
                self.controller.pause_range(range_record.owner_id)
                reaped += 1
            except Exception:
                logger.exception("failed to pause range owner=%s", range_record.owner_id)
        return reaped

    def _sweep_orphaned_resources(self) -> int:
        # A creator may already have made Docker resources but not finalized
        # plan_json yet. Never reconcile while any live reservation exists.
        if self.store.pending_count() or self.range_store.pending_count():
            return 0

        instance_records = self.store.all()
        range_records = self.range_store.all()
        expected_services = {
            service.name
            for record in instance_records
            for service in record.plan.services
        }
        expected_networks = {
            record.plan.network for record in instance_records if record.plan.network
        }
        for record in range_records:
            expected_services.add(record.plan.attacker_service.name)
            if record.plan.gateway_service:
                expected_services.add(record.plan.gateway_service.name)
            expected_networks.add(record.plan.network)

        reaped = 0
        try:
            managed_services = self.controller.docker.list_managed_services()
        except Exception:
            logger.exception("failed to list managed services for orphan reconciliation")
            return 0
        for service in managed_services:
            if service.name in expected_services:
                continue
            logger.warning("removing orphaned managed service %s", service.name)
            try:
                self.controller.cleanup_orphaned_service(service)
                reaped += 1
            except Exception:
                logger.exception("failed to remove orphaned service %s", service.name)

        try:
            managed_networks = self.controller.docker.list_managed_networks()
        except Exception:
            logger.exception("failed to list managed networks for orphan reconciliation")
            return reaped
        for network in managed_networks:
            if network.name in expected_networks:
                continue
            logger.warning("removing orphaned managed network %s", network.name)
            try:
                self.controller.docker.remove_network(network.name)
                reaped += 1
            except Exception:
                logger.exception("failed to remove orphaned network %s", network.name)
        return reaped
