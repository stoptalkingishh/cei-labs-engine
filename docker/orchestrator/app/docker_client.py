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

# create_service() retries this many times when Swarm rejects the create
# because one of the spec's own networks vanished between ensure_network()
# and the create call -- see _managed_missing_network's docstring for the
# race. One retry is enough in practice (the losing side re-creates the
# network and wins the second attempt); the extra attempt is headroom for a
# second teardown landing in the same window.
_SERVICE_CREATE_ATTEMPTS = 3

# Only networks this orchestrator itself creates per instance/range may be
# re-created on that retry path. Anything else named in a spec -- notably
# the stack-owned `cei-labs_challenge-edge` -- is provisioned by
# docker/stack.yml, and silently re-creating it here would produce a
# same-named network with orchestrator labels and no stack ownership.
_MANAGED_NETWORK_PREFIXES = ("chnet-", "chrange-")

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

    def resolve_image_digest(self, image_ref: str) -> str:
        """Best-effort pin of a mutable tag reference (`repo:tag`) to the
        content digest that tag *currently* resolves to in the local image
        store, e.g. `repo:latest` -> `repo@sha256:...`.

        Why this and not a real registry digest: these attacker/target/
        gateway images (ctf-kali-novnc, tcp-gateway, the Bandit/Krypton/
        Natas targets, ...) are built and loaded locally as part of this
        station's offline/air-gapped install path -- confirmed live on
        2026-07-23 (`sudo docker images`, `docker manifest inspect`; see
        docs/P1-FIX-LOG-2026-07-23.md) -- and are not reliably served by a
        reachable registry from every Swarm node's point of view. The
        original bug report's actual failure mode was a fresh Swarm task
        getting stuck in `Preparing`: when a node schedules a task for a
        service and doesn't already have a matching image cached, the
        engine falls back to a registry pull, and a pull that can't
        complete (private/unreachable registry, or a tag that was only
        ever pushed locally, never to GHCR) hangs rather than failing fast.
        Referencing the image by the digest already cached locally sidesteps
        that: Docker resolves a `repo@sha256:...` reference against its own
        content store first and only needs a registry if that exact digest
        is missing everywhere -- which the offline installer is what's
        supposed to guarantee across nodes (see docs/P1-FIX-LOG-2026-07-23.md
        for the operational caveat this implies: every node must have
        loaded the identical image content, or the digest won't be found
        there either).

        It is *also* the correct hardening against ordinary tag-mutation:
        it stops a same-named `:latest` rebuild/retag between when an image
        was tested and when Swarm places a fresh task from silently
        changing what actually runs, matching how docker/stack.yml already
        pins traefik/mariadb/redis by digest.

        Falls back to the original tag reference, unresolved (with a
        logged warning), if the image isn't present locally at all -- e.g.
        a genuine dev/test environment that expects to pull from a real
        registry. That is the one scenario this cannot protect against, by
        design: there is nothing local to pin to yet.
        """
        try:
            image = self._client.images.get(image_ref)
        except NotFound:
            logger.warning(
                "resolve_image_digest: %s not found in the local image store; "
                "using the tag reference as-is -- a fresh Swarm task for this "
                "image will depend on a live registry pull succeeding",
                image_ref,
            )
            return image_ref

        repo = image_ref.rsplit("@", 1)[0].rsplit(":", 1)[0]
        for repo_digest in image.attrs.get("RepoDigests") or []:
            digest_repo, _, digest = repo_digest.rpartition("@")
            if digest_repo == repo and digest:
                pinned = f"{repo}@{digest}"
                if pinned != image_ref:
                    logger.info("resolve_image_digest: pinned %s -> %s", image_ref, pinned)
                return pinned

        logger.warning(
            "resolve_image_digest: %s has no local RepoDigests (image may "
            "have been built/loaded without one being recorded); using the "
            "tag reference as-is",
            image_ref,
        )
        return image_ref

    @staticmethod
    def _managed_missing_network(exc: Exception, spec: "ServiceSpec") -> "str | None":
        """The name of one of `spec`'s own managed networks that Swarm says
        doesn't exist, or None if this error is anything else.

        Swarm reports it as a plain 500 with the name in the message
        ('network chnet-21-group-bandit not found'), not a typed error, so
        matching on the message is the only option -- but the name is matched
        against `spec.networks` rather than parsed out of the string, so a
        message-format change degrades to "don't retry" instead of
        misidentifying a network.

        Why this happens at all: ensure_network() returns early when the
        network already exists, and it cannot tell a healthy network apart
        from one that a concurrent teardown is midway through deleting
        (remove_network() retries for up to _TASK_DRAIN_TIMEOUT_SECONDS while
        endpoints drain). A player who relaunches the instant their box is
        torn down hits exactly that window: ensure_network() sees the old
        network and returns, the deletion completes, and the create then
        fails against a network that no longer exists. Observed live on a
        station 51 times in one event -- see docs/credential-lifecycle.md.

        This matters beyond a failed launch: the player is left with a broken
        environment, and the natural response is to click "Relaunch
        Environment", which is the one path that deliberately rotates their
        flags and passwords (purge_vaulted_secrets). A transient network race
        should not cost anyone their progress."""
        if not isinstance(exc, docker.errors.APIError):
            return None
        message = str(exc)
        if "not found" not in message:
            return None
        for name in spec.networks:
            if name in message and name.startswith(_MANAGED_NETWORK_PREFIXES):
                return name
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

        resolved_image = self.resolve_image_digest(spec.image)
        logger.info(
            "creating service %s (image=%s, resolved=%s, networks=%s)",
            spec.name, spec.image, resolved_image, spec.networks,
        )
        for attempt in range(1, _SERVICE_CREATE_ATTEMPTS + 1):
            try:
                return self._client.services.create(
                    image=resolved_image,
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
            except docker.errors.APIError as exc:
                missing = self._managed_missing_network(exc, spec)
                if missing is None or attempt == _SERVICE_CREATE_ATTEMPTS:
                    raise
                # Re-create it and try again. ensure_network() is itself
                # idempotent and waits for inspect visibility, so if the
                # concurrent teardown has meanwhile finished, this rebuilds
                # the network; if another caller already rebuilt it, this is
                # a no-op.
                logger.warning(
                    "service %s create failed: network %s vanished mid-create "
                    "(attempt %s/%s) -- re-creating it and retrying",
                    spec.name, missing, attempt, _SERVICE_CREATE_ATTEMPTS,
                )
                self.ensure_network(missing, internal=True)

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
