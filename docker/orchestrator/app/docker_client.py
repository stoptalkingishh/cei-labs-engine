"""docker/orchestrator/app/docker_client.py

Thin wrapper around the Docker Engine SDK, scoped to exactly what the
orchestrator needs: create/inspect/remove Swarm services, and create/remove
per-team overlay networks. Kept separate from instance_types.py so the
Docker-API specifics stay in one place and instance planning logic can be
unit-tested without a Docker daemon.
"""
import logging
import time
from dataclasses import dataclass, field

import docker
from docker.errors import NotFound
from docker.types import EndpointSpec, NetworkAttachmentConfig, Resources, RestartPolicy

logger = logging.getLogger(__name__)

ORCH_LABEL = "cei.orchestrator.managed"

# How long remove_service() waits for a removed service's last task to
# actually finish draining (stop, detach from its networks) before giving
# up -- see remove_service's docstring for why this matters.
_TASK_DRAIN_TIMEOUT_SECONDS = 15.0
_TASK_DRAIN_POLL_INTERVAL = 0.5
_NETWORK_READY_TIMEOUT_SECONDS = 5.0
_NETWORK_READY_POLL_INTERVAL = 0.05

# Terminal task states per the Swarm API -- a task in any other state might
# still hold its network attachment/port.
_TASK_TERMINAL_STATES = {"shutdown", "complete", "failed", "rejected", "orphaned", "remove"}


@dataclass
class ServiceSpec:
    name: str
    image: str
    networks: list[str]
    labels: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    # (published, target) pairs, rarely needed (SSH-style, or the Natas
    # attacker's SSH + direct-noVNC-fallback ports). Almost always empty or
    # single-item; a list because target-attacker's range attacker needs two.
    published_ports: "list[tuple[int, int]]" = field(default_factory=list)
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
    sysctls: dict[str, str] = field(default_factory=dict)


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
        # docker-py's high-level networks.create() immediately calls get()
        # on the returned ID. Under a 20-way Swarm network burst the daemon
        # has returned an ID before inspect_network can see it, producing a
        # transient 404 and leaving an orphan network. Use the low-level API
        # and explicitly wait for inspect visibility instead.
        response = self._client.api.create_network(
            name,
            driver="overlay",
            attachable=True,
            internal=internal,
            labels={ORCH_LABEL: "true"},
        )
        network_id = response["Id"]
        deadline = time.monotonic() + _NETWORK_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                self._client.api.inspect_network(network_id)
                return
            except NotFound:
                time.sleep(_NETWORK_READY_POLL_INTERVAL)
        raise RuntimeError(
            f"network {name} was created as {network_id} but did not become inspectable "
            f"within {_NETWORK_READY_TIMEOUT_SECONDS}s"
        )

    def remove_network(self, name: str) -> None:
        """Retries briefly rather than giving up on the first attempt --
        immediately after remove_service() a network can still show a
        lingering endpoint for a moment even though remove_service() itself
        already waited for the task to reach a terminal state (draining an
        endpoint is a separate, slightly-later step from the task stopping).
        This used to log a warning and defer entirely to the reaper's next
        sweep (up to REAP_INTERVAL_SECONDS later), which is what let an
        immediate relaunch race a same-named network that hadn't actually
        been removed yet -- see docs/adversarial-persona-findings-round-1.md."""
        try:
            net = self._client.networks.get(name)
        except NotFound:
            return

        deadline = time.monotonic() + _TASK_DRAIN_TIMEOUT_SECONDS
        last_exc = None
        while time.monotonic() < deadline:
            try:
                net.remove()
                return
            except docker.errors.APIError as exc:
                last_exc = exc
                time.sleep(_TASK_DRAIN_POLL_INTERVAL)
        # Still couldn't remove it after waiting -- log and let the reaper's
        # next sweep keep trying, same as before, but now only as a genuine
        # fallback rather than the normal path.
        logger.warning("could not remove network %s after %ss: %s", name, _TASK_DRAIN_TIMEOUT_SECONDS, last_exc)

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
        if spec.published_ports:
            # Only the hardened gateway may own published ports. Swarm's host
            # publish mode does not bind on this station when a service joins
            # an explicit overlay, so use the routing mesh here. Untrusted
            # targets and attackers never receive published ports or join the
            # ingress overlay. The station ingress pool must be sized for
            # repeated gateway churn; see docs/network-prerequisites.md.
            endpoint_spec = EndpointSpec(ports={published: target for published, target in spec.published_ports})

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
            sysctls=spec.sysctls or None,
            resources=Resources(
                mem_limit=spec.mem_limit_bytes,
                mem_reservation=spec.mem_reservation_bytes,
                cpu_limit=spec.cpu_limit_nanos,
            ),
            restart_policy=RestartPolicy(condition="on-failure"),
        )

    def remove_service(self, name: str) -> None:
        """Waits for the removed service's last task(s) to actually reach a
        terminal state before returning, instead of returning the instant
        the Swarm API accepts the removal request.

        Removing a Swarm service deletes its record from the raft store
        essentially immediately, but the underlying container/task keeps
        running for a bit longer while it actually stops and detaches from
        its network(s) -- an async drain, not part of the removal call
        itself. A caller that immediately tries to create a *new* service
        with the same name (teardown-then-relaunch, or a fresh instance
        landing on a just-freed port) can race that drain: Docker rejects
        the new service/network/port as still in use by the old one's
        lingering task. Verified live: 100% of "relaunch" attempts against
        an active instance failed with a 500 before this fix (see
        docs/adversarial-persona-findings-round-1.md), and a concurrent
        burst left an orphaned container still reachable -- serving a
        *different* team's data -- on a port CTFd's own API already
        reported as free.
        """
        svc = self.get_service(name)
        if svc is None:
            return
        service_id = svc.id
        logger.info("removing service %s", name)
        svc.remove()

        deadline = time.monotonic() + _TASK_DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                tasks = self._client.api.tasks(filters={"service": service_id})
            except docker.errors.NotFound:
                # The Engine API 404s (rather than returning []) once the
                # removed service has zero matching tasks left -- that's
                # the actual "fully drained" success case, not an error.
                return
            if not tasks or all(
                t.get("Status", {}).get("State") in _TASK_TERMINAL_STATES for t in tasks
            ):
                return
            time.sleep(_TASK_DRAIN_POLL_INTERVAL)
        logger.warning("service %s's task(s) still draining after %ss, proceeding anyway", name, _TASK_DRAIN_TIMEOUT_SECONDS)

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

    def list_managed_networks(self) -> list:
        return self._client.networks.list(filters={"label": ORCH_LABEL})
