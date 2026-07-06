"""docker/orchestrator/app/controller.py

Glues the pure planning logic (instance_types), the Docker API wrapper
(docker_client), and the in-memory registry (store) together. This is the
one place that knows the full create/teardown sequence, used by both the
HTTP routes (main.py) and the idle reaper (reaper.py).
"""
import logging
import time

from . import instance_types
from .docker_client import DockerOrchestratorClient
from .store import InstanceRecord, InstanceStore

logger = logging.getLogger(__name__)


class CapacityError(Exception):
    pass


class InstanceController:
    def __init__(self, docker_client: DockerOrchestratorClient, store: InstanceStore, base_domain: str, challenge_network: str, max_instances: int):
        self.docker = docker_client
        self.store = store
        self.base_domain = base_domain
        self.challenge_network = challenge_network
        self.max_instances = max_instances

    def create_or_get(self, instance_type: str, owner_id: str, instance_key: str, spec: dict):
        """Returns (InstancePlan, created: bool)."""
        existing = self.store.get(owner_id, instance_key)
        if existing is not None:
            existing.touch()
            return existing.plan, False

        if self.store.count() >= self.max_instances:
            raise CapacityError(f"at capacity ({self.max_instances} concurrent instances)")

        plan = instance_types.plan(
            instance_type, owner_id, instance_key, spec, self.base_domain, self.challenge_network
        )

        if plan.team_network:
            self.docker.ensure_team_network(plan.team_network)

        created_services = []
        try:
            for svc_spec in plan.services:
                created_services.append(self.docker.create_service(svc_spec))
        except Exception:
            logger.exception("failed creating services for %s/%s, rolling back", owner_id, instance_key)
            for svc_spec in plan.services:
                self.docker.remove_service(svc_spec.name)
            if plan.team_network:
                self.docker.remove_network(plan.team_network)
            raise

        self.store.put(InstanceRecord(plan=plan, created_at=time.time()))
        return plan, True

    def teardown(self, owner_id: str, instance_key: str) -> bool:
        record = self.store.get(owner_id, instance_key)
        if record is None:
            return False
        for svc_spec in record.plan.services:
            self.docker.remove_service(svc_spec.name)
        if record.plan.team_network:
            self.docker.remove_network(record.plan.team_network)
        self.store.remove(owner_id, instance_key)
        return True
