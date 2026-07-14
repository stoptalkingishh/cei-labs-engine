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
from .store import InstanceRecord, InstanceStore, RangeRecord, RangeStore

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
    ):
        self.docker = docker_client
        self.store = store
        self.range_store = range_store
        self.port_allocator = port_allocator
        self.base_domain = base_domain
        self.challenge_network = challenge_network
        self.max_instances = max_instances
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
            existing.touch()
            self.store.touch(owner_id, instance_key)
            if existing.plan.range_owner_id:
                self.range_store.touch(existing.plan.range_owner_id)
            return existing.plan, False

        if not owns_reservation:
            deadline = time.time() + _RESERVATION_WAIT_SECONDS
            while True:
                if self.store.reserve(owner_id, instance_key):
                    owns_reservation = True
                    break  # we won -- fall through and actually create it
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

        if self.store.count() > self.max_instances:
            self.store.release_reservation(owner_id, instance_key)
            raise CapacityError(f"at capacity ({self.max_instances} concurrent instances)")

        try:
            if replacement_record is not None:
                self._teardown_record(replacement_record)
            if instance_type == TARGET_ATTACKER:
                plan = self._create_range_target(owner_id, instance_key, spec)
            elif instance_type == SINGLE_TARGET:
                plan = self._create_single_target(owner_id, instance_key, spec)
            else:
                plan = instance_types.plan_web_app(owner_id, instance_key, spec, self.base_domain, self.challenge_network)
                self._create_services(plan.services)
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
        try:
            plan = instance_types.plan_single_target(owner_id, instance_key, spec, port, self.base_domain)
            self.docker.ensure_network(plan.network, internal=True)
            self._create_services(plan.services)
        except Exception:
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
                try:
                    range_plan = instance_types.plan_range_attacker(
                        owner_id, spec, ssh_port, novnc_port, self.base_domain, self.challenge_network
                    )
                    self.docker.ensure_network(range_plan.network, internal=True)
                    self._create_services([range_plan.attacker_service])
                except Exception:
                    self.port_allocator.release(ssh_port)
                    self.port_allocator.release(novnc_port)
                    self.range_store.release_reservation(owner_id)
                    raise
                self.range_store.finalize(owner_id, range_plan)
                range_record = RangeRecord(plan=range_plan, created_at=time.time())
        else:
            range_record.touch()
            self.range_store.touch(owner_id)

        target_plan = instance_types.plan_range_target(
            owner_id, instance_key, spec, range_record.plan.network, range_record.plan.access
        )
        self._create_services(target_plan.services)
        range_record.target_keys.add(instance_key)
        self.range_store.update(range_record)
        return target_plan

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
        for published, _target in range_record.plan.attacker_service.published_ports:
            self.port_allocator.release(published)
        self.docker.remove_network(range_record.plan.network)
        self.range_store.remove(owner_id)
        return True

    # ── Reboot ("restart in place") ──────────────────────────────────────────
    def reboot(self, owner_id: str, instance_key: str) -> bool:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            return False
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
