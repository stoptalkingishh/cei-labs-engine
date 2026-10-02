"""docker/orchestrator/app/reaper.py

Background thread with four independent jobs, all run every sweep:
  1. Idle pausing ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â non-destructively stops instances/ranges nobody has
     touched in a while ("touch" happens on every create_or_get() call, i.e.
     every time a participant (re-)opens the challenge in CTFd ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â see
     controller.py). Credentials/flags are preserved (controller.pause()) so
     the next touch resumes the SAME environment instead of a fresh one.
  2. Shutdown countdowns ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â non-destructively pauses an instance whose
     post-solve `shutdown_at` deadline has passed (see
     controller.schedule_shutdown/extend_shutdown), independent of whether
     it's otherwise "idle". Same credential-preserving pause as #1.
  3. Absolute lifetime enforcement: active touches cannot keep participant
     resources alive beyond the configured session ceiling. This IS
     destructive (controller.teardown()/teardown_range()) -- it's a genuine
     expiration, not a pause, and does end the credentials' validity; see
     docs/credential-lifecycle.md for what's exposed to warn callers before
     this fires.
  4. Crash/orphan recovery: reconciles label-managed Docker
     services/networks against the stores, then releases stale creation
     reservations -- in that order, and never while a reservation still has
     live Docker resources behind it, because that combination used to
     delete the containers of a launch that was merely slow (see
     sweep()/_release_stale_reservations()).

Only #3 above, and an explicit relaunch/reset request
(InstanceController.create_or_get(force_relaunch=True)) or delete
(teardown()/teardown_range()), ever end with different credentials than the
environment already had. #1 and #2 are pause/resume ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â see controller.py's
"Pause / resume" section for the mechanics.
"""
import logging
import re
import threading
import time
from datetime import datetime

from . import naming
from .controller import InstanceController
from .store import InstanceStore, RangeStore

logger = logging.getLogger(__name__)

# Docker timestamps its swarm objects as RFC3339 with *nanosecond* precision
# ("2026-01-02T03:04:05.123456789Z"). datetime.fromisoformat() only learned to
# accept more than exactly 3 or 6 fractional digits in 3.11, and this module's
# whole orphan-reconciliation guard is downstream of parsing succeeding -- on
# an older interpreter every managed resource would come back "unknown age",
# which the guard treats as "too young to touch", and orphan cleanup would
# quietly stop working on every deployment. Normalising the fraction ourselves
# costs one regex and makes the behaviour identical everywhere.
_RFC3339 = re.compile(
    r"^(?P<whole>\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2})(?P<frac>\.\d+)?(?P<tz>[Zz]|[+-]\d{2}:?\d{2})?$"
)


def _parse_docker_timestamp(raw: str) -> "datetime | None":
    match = _RFC3339.match(raw.strip())
    if match is None:
        return None
    # Pad/truncate whatever fraction came out to exactly 6 digits, which is
    # the one width fromisoformat() accepts on every version.
    microseconds = ((match.group("frac") or ".")[1:] + "000000")[:6]
    tz = match.group("tz") or "Z"
    if tz in ("Z", "z"):
        tz = "+00:00"
    elif ":" not in tz:
        tz = f"{tz[:3]}:{tz[3:]}"
    return datetime.fromisoformat(f"{match.group('whole')}.{microseconds}{tz}")


def _managed_resource_age_seconds(resource) -> "float | None":
    """How long ago Docker created this service/network, or None if the
    resource doesn't say. Docker's swarm objects expose it only as an
    RFC3339 string in ``attrs`` (``CreatedAt`` for services, ``Created``
    for networks), so an unrecognised shape is reported as "unknown age"
    rather than guessed at -- callers must treat unknown as "don't act on
    it", never as "it's old"."""
    attrs = getattr(resource, "attrs", None) or {}
    raw = attrs.get("CreatedAt") or attrs.get("Created")
    if not raw:
        logger.debug("managed resource %s reports no creation time", getattr(resource, "name", "?"))
        return None
    try:
        created = _parse_docker_timestamp(str(raw))
    except ValueError:
        created = None
    if created is None:
        logger.warning("could not parse managed resource creation time %r", raw)
        return None
    return time.time() - created.timestamp()


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
        # Order is load-bearing, not cosmetic. Orphan reconciliation runs
        # BEFORE stale reservations are released: the two used to run the
        # other way round, which meant the release of one slow-but-alive
        # creation's reservation dropped pending_count() to 0 and the very
        # next sweep deleted the containers and network that creation was
        # still in the middle of building -- while the launching request
        # went on to report success. Reconciling first means a reservation
        # that still exists is always treated as in flight and left alone.
        reaped += self._sweep_orphaned_resources()
        reaped += self._release_stale_reservations()
        reaped += self._sweep_due_shutdowns()
        reaped += self._sweep_expired_instances()
        reaped += self._sweep_idle_instances()
        reaped += self._sweep_expired_ranges()
        reaped += self._sweep_idle_ranges()
        return reaped

    def _live_managed_resource_names(self) -> "set[str] | None":
        """Names of every label-managed Docker service and network that
        exists right now, or None if they can't be listed at all."""
        try:
            names = {service.name for service in self.controller.docker.list_managed_services()}
            names |= {network.name for network in self.controller.docker.list_managed_networks()}
        except Exception:
            logger.exception("failed to list managed resources")
            return None
        return names

    def _release_stale_reservations(self) -> int:
        # A reservation's age alone can't tell a crashed worker from a
        # live-but-slow one: a launch blocked on a slow image pull sits in
        # this state for longer than RESERVATION_TIMEOUT_SECONDS, and
        # abandoning *its* row is precisely what made the next orphan sweep
        # delete the containers it was still creating. So a pending
        # reservation is only treated as abandoned once nothing that could
        # have been created by it is still live in Docker -- the same
        # evidence the orphan sweep uses to call a resource an orphan in the
        # first place. Failing to list the resources at all protects
        # everything, since that's the same uncertainty.
        live = self._live_managed_resource_names()
        protected_instances: set = set()
        protected_ranges: set = set()
        if live is None:
            protected_instances = set(self.store.pending_reservations())
            protected_ranges = set(self.range_store.pending_reservations())
        else:
            protected_instances = {
                (owner_id, instance_key)
                for owner_id, instance_key in self.store.pending_reservations()
                if naming.reservation_resource_names(owner_id, instance_key) & live
            }
            protected_ranges = {
                (owner_id,)
                for (owner_id,) in self.range_store.pending_reservations()
                if naming.range_reservation_resource_names(owner_id) & live
            }
            # A target-attacker's own resources are all created *after* the
            # shared per-owner range (controller._create_range_target builds
            # the range attacker first, the target last), so while that range
            # creation is provably in flight -- its attacker is live, which
            # is what put it in protected_ranges above -- its instance rows
            # look resource-less even though they cannot possibly have
            # finished. The same live-resource evidence that protects the
            # range therefore has to protect every instance reservation of
            # that owner waiting on it, or the row ages out mid-launch,
            # finalize() raises ReservationLostError, and CTFd reports 503
            # for a launch still under way. This protection is bounded: it
            # lasts exactly as long as the range reservation is pending, so
            # once the range creation resolves (finalized or failed) a
            # crashed worker's instance row ages out on the next pass.
            protected_instances |= {
                (owner_id, instance_key)
                for owner_id, instance_key in self.store.pending_reservations()
                if (owner_id,) in protected_ranges
            }
        if protected_instances or protected_ranges:
            logger.info(
                "keeping %s instance and %s range creation reservation(s) with live Docker resources",
                len(protected_instances), len(protected_ranges),
            )
        released = self.store.release_stale_reservations(
            self.reservation_timeout_seconds, exclude=protected_instances
        )
        released += self.range_store.release_stale_reservations(
            self.reservation_timeout_seconds, exclude=protected_ranges
        )
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

    def _too_fresh_to_be_an_orphan(self, resource) -> bool:
        """True if this managed resource is younger than
        RESERVATION_TIMEOUT_SECONDS, i.e. still possibly part of a creation
        that is in flight.

        Second guard behind the pending_count() check above, for when that
        check can't help: a creator blocked on a slow image pull registers
        its Swarm service and network first and only finalizes its store row
        at the very end, so the row alone doesn't tell you whether the
        resources it has already made belong to someone mid-launch. Anything
        the reaper can't even prove is older than the creation timeout is
        left for the next sweep rather than deleted out from under a launch
        that is still running."""
        age = _managed_resource_age_seconds(resource)
        if age is None:
            # Docker didn't tell us when this appeared. Can't prove it's
            # old, so don't act on it.
            return True
        return age < self.reservation_timeout_seconds

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
            if self._too_fresh_to_be_an_orphan(service):
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
            if self._too_fresh_to_be_an_orphan(network):
                continue
            logger.warning("removing orphaned managed network %s", network.name)
            try:
                self.controller.docker.remove_network(network.name)
                reaped += 1
            except Exception:
                logger.exception("failed to remove orphaned network %s", network.name)
        return reaped
