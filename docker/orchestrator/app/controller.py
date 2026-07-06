"""docker/orchestrator/app/controller.py

Glues the pure planning logic (instance_types), the Docker API wrapper
(docker_client), the in-memory registries (store), and the port allocator
together. This is the one place that knows the full lifecycle — create,
reboot, relaunch, teardown, range management, and the post-solve shutdown
countdown — used by both the HTTP routes (main.py) and the idle reaper
(reaper.py).
"""
import logging
import time

from . import instance_types
from .docker_client import DockerOrchestratorClient
from .instance_types import SINGLE_TARGET, TARGET_ATTACKER
from .ports import PortAllocator
from .store import InstanceRecord, InstanceStore, RangeRecord, RangeStore

logger = logging.getLogger(__name__)


class CapacityError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ShutdownNotPendingError(Exception):
    pass


class ExtensionsExhaustedError(Exception):
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
        """Returns (InstancePlan, created: bool)."""
        existing = self.store.get(owner_id, instance_key)
        if existing is not None:
            if force_relaunch:
                self.teardown(owner_id, instance_key)
            else:
                existing.touch()
                if existing.plan.range_owner_id:
                    self.range_store.touch(existing.plan.range_owner_id)
                return existing.plan, False

        if self.store.count() >= self.max_instances:
            raise CapacityError(f"at capacity ({self.max_instances} concurrent instances)")

        if instance_type == TARGET_ATTACKER:
            plan = self._create_range_target(owner_id, instance_key, spec)
        elif instance_type == SINGLE_TARGET:
            plan = self._create_single_target(owner_id, instance_key, spec)
        else:
            plan = instance_types.plan_web_app(owner_id, instance_key, spec, self.base_domain, self.challenge_network)
            self._create_services(plan.services)

        self.store.put(InstanceRecord(plan=plan, created_at=time.time()))
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
        range_record = self.range_store.get(owner_id)
        if range_record is None:
            range_plan = instance_types.plan_range_attacker(owner_id, spec, self.base_domain, self.challenge_network)
            self.docker.ensure_network(range_plan.network, internal=True)
            self._create_services([range_plan.attacker_service])
            range_record = RangeRecord(plan=range_plan, created_at=time.time())
            self.range_store.put(range_record)
        else:
            range_record.touch()

        target_plan = instance_types.plan_range_target(
            owner_id, instance_key, spec, range_record.plan.network, range_record.plan.access
        )
        self._create_services(target_plan.services)
        range_record.target_keys.add(instance_key)
        return target_plan

    # ── Teardown ──────────────────────────────────────────────────────────────
    def teardown(self, owner_id: str, instance_key: str) -> bool:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            return False

        for svc_spec in record.plan.services:
            self.docker.remove_service(svc_spec.name)
            if svc_spec.published_port:
                self.port_allocator.release(svc_spec.published_port[0])

        if record.plan.network:
            self.docker.remove_network(record.plan.network)

        if record.plan.range_owner_id:
            range_record = self.range_store.get(record.plan.range_owner_id)
            if range_record:
                range_record.target_keys.discard(instance_key)

        self.store.remove(owner_id, instance_key)
        return True

    def teardown_range(self, owner_id: str) -> bool:
        range_record = self.range_store.get(owner_id)
        if range_record is None:
            return False
        for instance_key in list(range_record.target_keys):
            self.teardown(owner_id, instance_key)
        self.docker.remove_service(range_record.plan.attacker_service.name)
        self.docker.remove_network(range_record.plan.network)
        self.range_store.remove(owner_id)
        return True

    # ── Reboot ("restart in place") ──────────────────────────────────────────
    def reboot(self, owner_id: str, instance_key: str) -> bool:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            return False
        record.touch()
        ok = True
        for svc_spec in record.plan.services:
            ok = self.docker.restart_service(svc_spec.name) and ok
        return ok

    def reboot_range_attacker(self, owner_id: str) -> bool:
        range_record = self.range_store.get(owner_id)
        if range_record is None:
            return False
        range_record.touch()
        return self.docker.restart_service(range_record.plan.attacker_service.name)

    # ── Post-solve shutdown countdown ────────────────────────────────────────
    def schedule_shutdown(self, owner_id: str, instance_key: str, delay_seconds: int) -> float:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            raise NotFoundError(f"no instance for {owner_id}/{instance_key}")
        record.shutdown_at = time.time() + delay_seconds
        record.extensions_used = 0
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
        return record.shutdown_at

    def cancel_shutdown(self, owner_id: str, instance_key: str) -> None:
        record = self.store.get(owner_id, instance_key)
        if record is not None:
            record.shutdown_at = None
            record.extensions_used = 0
