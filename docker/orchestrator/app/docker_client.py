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
    # Empty cap_drop (the default) means "use Docker's own default
    # capability set" -- explicit cap_drop=["ALL"] + a narrow cap_add is
    # how a challenge type opts into the hardened, audited set instead.
    # docker-py 7.1.0's Swarm services API has no no_new_privileges or
    # pids_limit equivalent (checked docker.types.Privileges/Resources
    # directly -- neither field exists), so those two aren't available
    # through this client at all right now.
    cap_drop: list[str] = field(default_factory=list)
    cap_add: list[str] = field(default_factory=list)
    read_only: bool = False
    cpu_limit_nanos: "int | None" = None  # e.g. 1_000_000_000 == 1.0 CPU


class DockerOrchestratorClient:
    def __init__(self, base_url: str):
        self._client = docker.DockerClient(base_url=base_url)

    # ── Networks ─────────────────────────────────────────────────────────────
    def ensure_network(self, name: str, internal: bool = True) -> None:
        """Creates the network if missing. `internal=True` (the default, and
        what every challenge-related network should use) means Docker gives
        it no outbound route at all — a real airgap, not just access control
        — independent of whether services on it also publish ports or share
        another (non-internal) network like `challenge-edge` for Traefik."""
        try:
            self._client.networks.get(name)
            return
        except NotFound:
            pass
        logger.info("creating overlay network %s (internal=%s)", name, internal)
        self._client.networks.create(
            name,
            driver="overlay",
            attachable=True,
            internal=internal,
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
            cap_drop=spec.cap_drop or None,
            cap_add=spec.cap_add or None,
            read_only=spec.read_only,
            resources=Resources(
                mem_limit=spec.mem_limit_bytes,
                mem_reservation=spec.mem_reservation_bytes,
                cpu_limit=spec.cpu_limit_nanos,
            ),
            restart_policy=RestartPolicy(condition="on-failure"),
        )

    def remove_service(self, name: str) -> None:
        svc = self.get_service(name)
        if svc is None:
            return
        logger.info("removing service %s", name)
        svc.remove()

    def restart_service(self, name: str) -> bool:
        """"Reboot Host": restarts the container(s) in place — same service,
        same network identity/published port, no state carried over inside
        the container itself. Uses docker-py's force_update(), which bumps
        the task template's force-update counter and triggers a fresh
        rolling restart without changing any other config."""
        svc = self.get_service(name)
        if svc is None:
            return False
        logger.info("restarting service %s in place", name)
        svc.force_update()
        return True

    def list_managed_services(self) -> list:
        return self._client.services.list(filters={"label": ORCH_LABEL})
