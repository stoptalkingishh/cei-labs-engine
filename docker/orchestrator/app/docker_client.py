"""docker/orchestrator/app/docker_client.py

Thin wrapper around the Docker Engine SDK, scoped to exactly what the
orchestrator needs: create/inspect/remove Swarm services, and create/remove
per-team overlay networks. Kept separate from instance_types.py so the
Docker-API specifics stay in one place and instance planning logic can be
unit-tested without a Docker daemon.
"""
import logging
from dataclasses import dataclass, field

import docker
from docker.errors import NotFound
from docker.types import EndpointSpec, NetworkAttachmentConfig, Resources, RestartPolicy

logger = logging.getLogger(__name__)

ORCH_LABEL = "cei.orchestrator.managed"


@dataclass
class ServiceSpec:
    name: str
    image: str
    networks: list[str]
    labels: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    published_port: "tuple[int, int] | None" = None  # (published, target), rarely needed (SSH-style)
    mem_limit_bytes: int = 512 * 1024 * 1024
    mem_reservation_bytes: int = 128 * 1024 * 1024


class DockerOrchestratorClient:
    def __init__(self, base_url: str):
        self._client = docker.DockerClient(base_url=base_url)

    # ── Networks ─────────────────────────────────────────────────────────────
    def ensure_team_network(self, name: str) -> None:
        try:
            self._client.networks.get(name)
            return
        except NotFound:
            pass
        logger.info("creating per-team overlay network %s", name)
        self._client.networks.create(
            name,
            driver="overlay",
            attachable=True,
            labels={ORCH_LABEL: "true"},
        )

    def remove_network(self, name: str) -> None:
        try:
            net = self._client.networks.get(name)
        except NotFound:
            return
        try:
            net.remove()
        except docker.errors.APIError as exc:
            # Network may still be draining a just-removed service's endpoint;
            # the reaper/caller is expected to retry on next sweep.
            logger.warning("could not remove network %s yet: %s", name, exc)

    # ── Services ─────────────────────────────────────────────────────────────
    def get_service(self, name: str):
        try:
            return self._client.services.get(name)
        except NotFound:
            return None

    def create_service(self, spec: ServiceSpec):
        existing = self.get_service(spec.name)
        if existing is not None:
            return existing

        endpoint_spec = None
        if spec.published_port:
            published, target = spec.published_port
            endpoint_spec = EndpointSpec(ports={published: target})

        logger.info("creating service %s (image=%s, networks=%s)", spec.name, spec.image, spec.networks)
        return self._client.services.create(
            image=spec.image,
            name=spec.name,
            env=[f"{k}={v}" for k, v in spec.env.items()],
            networks=[NetworkAttachmentConfig(target=n) for n in spec.networks],
            labels={**spec.labels, ORCH_LABEL: "true"},
            endpoint_spec=endpoint_spec,
            resources=Resources(
                mem_limit=spec.mem_limit_bytes,
                mem_reservation=spec.mem_reservation_bytes,
            ),
            restart_policy=RestartPolicy(condition="on-failure"),
        )

    def remove_service(self, name: str) -> None:
        svc = self.get_service(name)
        if svc is None:
            return
        logger.info("removing service %s", name)
        svc.remove()

    def list_managed_services(self) -> list:
        return self._client.services.list(filters={"label": ORCH_LABEL})
