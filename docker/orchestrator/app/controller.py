"""docker/orchestrator/app/controller.py

Glues the pure planning logic (instance_types), the Docker API wrapper
(docker_client), the SQLite-backed registries (store), and the port
allocator together. This is the one place that knows the full lifecycle —
create, reboot, relaunch, teardown, range management, and the post-solve
shutdown countdown — used by both the HTTP routes (main.py) and the idle
reaper (reaper.py).
"""
import logging
import time

from . import instance_types
from .docker_client import DockerOrchestratorClient
from .instance_types import SINGLE_TARGET, TARGET_ATTACKER
from .ports import PortAllocator
from .store import (
    InstanceRecord,
    InstanceStore,
    RangeRecord,
    RangeStore,
    ReservationCapacityError,
)

logger = logging.getLogger(__name__)

# How long a request will wait for a *different* in-flight request (won the
# reserve() race first) to finish creating before giving up. Real creations
# are fast (see TRACKER.md's load-test numbers), so this is generous
# headroom, not a tuned timeout.
_RESERVATION_WAIT_SECONDS = 10.0
_RESERVATION_POLL_INTERVAL = 0.1


class CapacityError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ShutdownNotPendingError(Exception):
    pass


class ExtensionsExhaustedError(Exception):
    pass


class InstanceInitializingError(Exception):
    """Another request already won the race to create this instance/range
    and is still in the middle of doing so. The caller should retry."""
    pass


class InstanceController:
    def __init__(
        self,
        docker_client: DockerOrchestratorClient,
        store: InstanceStore,
        range_store: RangeStore,
        port_allocator: PortAllocator,
        base_domain: str,
        challenge_network: str,
        max_instances: int,
        shutdown_max_extensions: int,
        max_instances_per_owner: "int | None" = None,
        workload_quota: "instance_types.WorkloadQuota | None" = None,
    ):
        self.docker = docker_client
        self.store = store
        self.range_store = range_store
        self.port_allocator = port_allocator
        self.base_domain = base_domain
        self.challenge_network = challenge_network
        self.max_instances = max_instances
        self.max_instances_per_owner = (
            max_instances if max_instances_per_owner is None else max_instances_per_owner
        )
        self.workload_quota = workload_quota or instance_types.DEFAULT_WORKLOAD_QUOTA
        self.shutdown_max_extensions = shutdown_max_extensions

    # ── Create / reuse ───────────────────────────────────────────────────────
    def create_or_get(self, instance_type: str, owner_id: str, instance_key: str, spec: dict, force_relaunch: bool = False):
        """Returns (InstancePlan, created: bool).

        Race-free by construction: `store.reserve()` is a single atomic
        INSERT gated by a PRIMARY KEY, shared across every gunicorn worker
        process via the same SQLite file (see store.py's module docstring).
        Only the request that wins the reservation ever calls the Docker
        API — a loser waits for the winner's plan_json to land, or (if the
        winner's creation failed and released the slot) retries the
        reservation itself. This replaces a plain get-then-put that let two
        workers both pass the "does this exist yet" check and both create
        real containers -- verified live in TRACKER.md.
        """
        owns_reservation = False
        replacement_record = None
        existing = self.store.get(owner_id, instance_key)
        if force_relaunch:
            # Atomically claim the finalized row before touching Docker. Only
            # one worker gets the old plan; concurrent relaunch callers see a
            # pending reservation and wait for this replacement to finish.
            replacement_record = self.store.claim_for_replacement(owner_id, instance_key)
            owns_reservation = replacement_record is not None
        elif existing is not None:
            if existing.stopped:
                # Non-destructive idle/shutdown pause -- recreate the exact
                # same Docker resources from the persisted plan (same env,
                # same generated credentials/flags) instead of replanning.
                # See pause()/_resume_range_if_stopped()'s docstrings.
                self._resume_record(existing)
            existing.touch()
            self.store.touch(owner_id, instance_key)
            if existing.plan.range_owner_id:
                self._resume_range_if_stopped(existing.plan.range_owner_id)
                self.range_store.touch(existing.plan.range_owner_id)
            return existing.plan, False

        if not owns_reservation:
            deadline = time.time() + _RESERVATION_WAIT_SECONDS
            while True:
                try:
                    if self.store.reserve(
                        owner_id,
                        instance_key,
                        max_instances=self.max_instances,
                        max_instances_per_owner=self.max_instances_per_owner,
                    ):
                        owns_reservation = True
                        break  # we won -- fall through and actually create it
                except ReservationCapacityError as exc:
                    if exc.scope == "owner":
                        raise CapacityError(f"owner at quota ({exc.limit} concurrent instances)") from exc
                    raise CapacityError(f"at capacity ({exc.limit} concurrent instances)") from exc
                record = self.store.get(owner_id, instance_key)
                if record is not None:
                    record.touch()
                    self.store.touch(owner_id, instance_key)
                    return record.plan, False
                if time.time() >= deadline:
                    raise InstanceInitializingError(
                        f"instance for {owner_id}/{instance_key} is still being created by another request, retry shortly"
                    )
                time.sleep(_RESERVATION_POLL_INTERVAL)

        try:
            if replacement_record is not None:
                self._teardown_record(replacement_record)
            if instance_type == TARGET_ATTACKER:
                plan = self._create_range_target(owner_id, instance_key, spec)
            elif instance_type == SINGLE_TARGET:
                plan = self._create_single_target(owner_id, instance_key, spec)
            else:
                plan = instance_types.plan_web_app(
                    owner_id,
                    instance_key,
                    spec,
                    self.base_domain,
                    self.challenge_network,
                    self.workload_quota,
                )
                try:
                    self.docker.ensure_network(plan.network, internal=True)
                    self._create_services(plan.services)
                except Exception:
                    self.docker.remove_network(plan.network)
                    raise
        except Exception:
            self.store.release_reservation(owner_id, instance_key)
            raise

        self.store.finalize(owner_id, instance_key, plan)
        return plan, True

    def _create_services(self, services) -> None:
        created = []
        try:
            for svc_spec in services:
                created.append(self.docker.create_service(svc_spec))
        except Exception:
            logger.exception("failed creating services %s, rolling back", [s.name for s in services])
            for svc_spec in services:
                self.docker.remove_service(svc_spec.name)
            raise

    def _create_single_target(self, owner_id: str, instance_key: str, spec: dict):
        port = self.port_allocator.allocate()
        plan = None
        try:
            plan = instance_types.plan_single_target(
                owner_id, instance_key, spec, port, self.base_domain, self.workload_quota
            )
            self.docker.ensure_network(plan.network, internal=True)
            self._create_services(plan.services)
        except Exception:
            if plan is not None and plan.network:
                self.docker.remove_network(plan.network)
            self.port_allocator.release(port)
            raise
        return plan

    def _create_range_target(self, owner_id: str, instance_key: str, spec: dict):
        # Same atomic-reservation pattern as create_or_get, scoped to the
        # shared per-owner range attacker: only one worker ever creates it,
        # even though many (owner_id, instance_key) target creations for
        # different targets in the same range will call this concurrently.
        range_record = self.range_store.get(owner_id)
        if range_record is None:
            deadline = time.time() + _RESERVATION_WAIT_SECONDS
            while range_record is None:
                if self.range_store.reserve(owner_id):
                    break
                range_record = self.range_store.get(owner_id)
                if range_record is not None:
                    break
                if time.time() >= deadline:
                    raise InstanceInitializingError(
                        f"range attacker for {owner_id} is still being created by another request, retry shortly"
                    )
                time.sleep(_RESERVATION_POLL_INTERVAL)

            if range_record is None:  # we won the reservation -- create it for real
                ssh_port = self.port_allocator.allocate()
                novnc_port = self.port_allocator.allocate()
                range_plan = None
                try:
                    range_plan = instance_types.plan_range_attacker(
                        owner_id,
                        spec,
                        ssh_port,
                        novnc_port,
                        self.base_domain,
                        self.challenge_network,
                        self.workload_quota,
                    )
                    self.docker.ensure_network(range_plan.network, internal=True)
                    self._create_services([range_plan.attacker_service, range_plan.gateway_service])
                except Exception:
                    if range_plan is not None:
                        self.docker.remove_network(range_plan.network)
                    self.port_allocator.release(ssh_port)
                    self.port_allocator.release(novnc_port)
                    self.range_store.release_reservation(owner_id)
                    raise
                self.range_store.finalize(owner_id, range_plan)
                range_record = RangeRecord(plan=range_plan, created_at=time.time())
        else:
            if range_record.stopped:
                self._resume_range_if_stopped(owner_id)
                range_record = self.range_store.get(owner_id)
            range_record.touch()
            self.range_store.touch(owner_id)

        target_plan = instance_types.plan_range_target(
            owner_id,
            instance_key,
            spec,
            range_record.plan.network,
            range_record.plan.access,
            self.workload_quota,
        )
        self._create_services(target_plan.services)
        range_record.target_keys.add(instance_key)
        self.range_store.update(range_record)
        return target_plan

    # ── Pause / resume ("non-destructive stop") ──────────────────────────────
    # Idle timeout and the post-solve shutdown countdown are automatic
    # housekeeping, not an explicit "reset my environment" request from the
    # learner. Neither may rotate credentials or flags -- so instead of the
    # destructive teardown() path (which deletes the store row and forces a
    # fresh plan_*() call, generating brand-new secrets, on next access),
    # both now go through pause(): remove the running Docker resources but
    # keep the record -- and therefore the exact plan/env that was baked in
    # at creation time -- so create_or_get()/reboot() can recreate the
    # identical container the next time the learner shows up. Only
    # create_or_get(force_relaunch=True) (an explicit relaunch/reset) and
    # teardown()/teardown_range() (explicit delete, or true absolute-lifetime
    # expiry -- see reaper._sweep_expired_instances) still end with a fresh
    # plan and therefore new credentials.
    def _resume_record(self, record: InstanceRecord) -> None:
        """Recreate a paused instance's Docker resources from its persisted
        plan -- same env (same VNC/OPERATOR password, same flags) -- and
        clear its stopped flag. Ports were never released on pause (see
        pause()), so `access` (advertised connect host/port) stays valid
        too."""
        if record.plan.network:
            self.docker.ensure_network(record.plan.network, internal=True)
        self._create_services(record.plan.services)
        record.stopped = False
        self.store.update(record)

    def _resume_range_if_stopped(self, owner_id: str) -> None:
        range_record = self.range_store.get(owner_id)
        if range_record is None or not range_record.stopped:
            return
        self.docker.ensure_network(range_record.plan.network, internal=True)
        services = [range_record.plan.attacker_service]
        if range_record.plan.gateway_service:
            services.append(range_record.plan.gateway_service)
        self._create_services(services)
        range_record.stopped = False
        self.range_store.update(range_record)

    def pause(self, owner_id: str, instance_key: str) -> bool:
        """Non-destructive stop used by the idle reaper and the post-solve
        shutdown countdown. Removes the live Docker service(s)/network but
        keeps the store row (plan, credentials, flags) intact and marks it
        `stopped` so the next create_or_get()/reboot() recreates the exact
        same environment rather than generating a new one. Deliberately does
        NOT release this instance's published ports (see
        PortAllocator/_create_single_target) so a resumed instance keeps the
        same connect_host:connect_port a learner may already have been
        given. Returns False if there's nothing to pause (no record, or
        already paused) -- idempotent/safe for the reaper to call every
        sweep."""
        record = self.store.get(owner_id, instance_key)
        if record is None or record.stopped:
            return False
        for svc_spec in record.plan.services:
            self.docker.remove_service(svc_spec.name)
        if record.plan.network:
            self.docker.remove_network(record.plan.network)
        record.stopped = True
        # A pause supersedes any pending post-solve countdown -- there's
        # nothing left running to shut down, and carrying a stale
        # shutdown_at across a later resume would immediately re-trigger
        # teardown on the next sweep.
        record.shutdown_at = None
        record.extensions_used = 0
        self.store.update(record)
        return True

    def pause_range(self, owner_id: str) -> bool:
        """Same as pause(), for a range's shared attacker+gateway. The
        range's overlay network is deliberately left in place (targets on
        it are paused/resumed independently via pause()/create_or_get()),
        and its published SSH/noVNC ports are kept reserved for the same
        reason pause() keeps an instance's ports."""
        range_record = self.range_store.get(owner_id)
        if range_record is None or range_record.stopped:
            return False
        self.docker.remove_service(range_record.plan.attacker_service.name)
        if range_record.plan.gateway_service:
            self.docker.remove_service(range_record.plan.gateway_service.name)
        range_record.stopped = True
        self.range_store.update(range_record)
        return True

    # ── Teardown ──────────────────────────────────────────────────────────────
    def _teardown_record(self, record: InstanceRecord) -> None:
        """Remove Docker resources described by an already-claimed record.

        Store state is deliberately not changed here. The caller owns the
        pending reservation and must either finalize a replacement or release
        it. This prevents a late teardown from deleting a newer lifecycle
        operation's state.
        """
        instance_key = record.instance_key
        if record is None:
            return

        for svc_spec in record.plan.services:
            self.docker.remove_service(svc_spec.name)
            for published, _target in svc_spec.published_ports:
                self.port_allocator.release(published)

        if record.plan.network:
            self.docker.remove_network(record.plan.network)

        if record.plan.range_owner_id:
            range_record = self.range_store.get(record.plan.range_owner_id)
            if range_record:
                range_record.target_keys.discard(instance_key)
                self.range_store.update(range_record)

    def teardown(self, owner_id: str, instance_key: str) -> bool:
        record = self.store.claim_for_replacement(owner_id, instance_key)
        if record is None:
            return False
        try:
            self._teardown_record(record)
        finally:
            self.store.release_reservation(owner_id, instance_key)
        return True

    def teardown_range(self, owner_id: str) -> bool:
        range_record = self.range_store.get(owner_id)
        if range_record is None:
            return False
        for instance_key in list(range_record.target_keys):
            self.teardown(owner_id, instance_key)
        self.docker.remove_service(range_record.plan.attacker_service.name)
        if range_record.plan.gateway_service:
            self.docker.remove_service(range_record.plan.gateway_service.name)
            for published, _target in range_record.plan.gateway_service.published_ports:
                self.port_allocator.release(published)
        else:  # backward-compatible teardown of pre-gateway persisted plans
            for published, _target in range_record.plan.attacker_service.published_ports:
                self.port_allocator.release(published)
        self.docker.remove_network(range_record.plan.network)
        self.range_store.remove(owner_id)
        return True

    def cleanup_orphaned_service(self, service) -> None:
        """Remove an untracked managed service and release its published ports."""
        name = service.name
        published_ports = list(getattr(service, "published_ports", []) or [])
        if not published_ports:
            attrs = getattr(service, "attrs", {}) or {}
            endpoint = attrs.get("Endpoint", {}).get("Spec", {})
            endpoint = endpoint or attrs.get("Spec", {}).get("EndpointSpec", {})
            published_ports = [
                (port["PublishedPort"], port.get("TargetPort"))
                for port in endpoint.get("Ports", []) or []
                if port.get("PublishedPort") is not None
            ]
        self.docker.remove_service(name)
        for published, _target in published_ports:
            self.port_allocator.release(published)

    # ── Reboot ("restart in place") ──────────────────────────────────────────
    def reboot(self, owner_id: str, instance_key: str) -> bool:
        """Restarts the container(s) in place, same credentials/flags either
        way: force_update() for a live instance (env is part of the existing
        task spec, untouched), or -- if the instance is currently paused --
        recreate it from the persisted plan, exactly like a normal resume."""
        record = self.store.get(owner_id, instance_key)
        if record is None:
            return False
        if record.stopped:
            self._resume_record(record)
            record.touch()
            self.store.touch(owner_id, instance_key)
            if record.plan.range_owner_id:
                self._resume_range_if_stopped(record.plan.range_owner_id)
            return True
        record.touch()
        self.store.touch(owner_id, instance_key)
        ok = True
        for svc_spec in record.plan.services:
            ok = self.docker.restart_service(svc_spec.name) and ok
        return ok

    def reboot_range_attacker(self, owner_id: str) -> bool:
        range_record = self.range_store.get(owner_id)
        if range_record is None:
            return False
        if range_record.stopped:
            self._resume_range_if_stopped(owner_id)
            self.range_store.touch(owner_id)
            return True
        range_record.touch()
        self.range_store.touch(owner_id)
        return self.docker.restart_service(range_record.plan.attacker_service.name)

    # ── Post-solve shutdown countdown ────────────────────────────────────────
    def schedule_shutdown(self, owner_id: str, instance_key: str, delay_seconds: int) -> float:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            raise NotFoundError(f"no instance for {owner_id}/{instance_key}")
        record.shutdown_at = time.time() + delay_seconds
        record.extensions_used = 0
        self.store.update(record)
        return record.shutdown_at

    def extend_shutdown(self, owner_id: str, instance_key: str, extend_seconds: int) -> float:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            raise NotFoundError(f"no instance for {owner_id}/{instance_key}")
        if record.shutdown_at is None:
            raise ShutdownNotPendingError("no shutdown is currently scheduled for this instance")
        if record.extensions_used >= self.shutdown_max_extensions:
            raise ExtensionsExhaustedError(f"maximum of {self.shutdown_max_extensions} extensions already used")
        record.extensions_used += 1
        record.shutdown_at = time.time() + extend_seconds
        self.store.update(record)
        return record.shutdown_at

    def cancel_shutdown(self, owner_id: str, instance_key: str) -> None:
        record = self.store.get(owner_id, instance_key)
        if record is not None:
            record.shutdown_at = None
            record.extensions_used = 0
            self.store.update(record)
