"""docker/orchestrator/app/instance_types.py

Pure planning logic: given a request body, decide what Docker services (and
networks) are needed. No Docker API calls happen here, which is what makes
this module unit-testable without a daemon.

Three instance types, per the migration plan:
  - web-app:          one container, HTTP-routed through Traefik on the
                       shared `challenge-edge` network. Covers Juice Shop.
  - single-target:    one container on its OWN dedicated, airgapped
                       (Docker `internal: true`) network, reachable only via
                       a directly published port (SSH or whatever the
                       challenge needs) — no Traefik involved at all.
  - target-attacker:  a per-team "range": one shared attacker (browser
                       noVNC, Traefik-routed) plus N targets, all on one
                       persistent, airgapped network scoped to the owner
                       (not the individual challenge) — see RangePlan below.
                       A target is only ever reachable from its range's own
                       attacker, never from Traefik, other teams, or CTFd.
"""
from dataclasses import dataclass, field

from .docker_client import ServiceSpec
from . import naming

WEB_APP = "web-app"
SINGLE_TARGET = "single-target"
TARGET_ATTACKER = "target-attacker"

VALID_TYPES = (WEB_APP, SINGLE_TARGET, TARGET_ATTACKER)

# Phase 6 hardening: cap_drop=["ALL"] plus back only what's actually
# needed, confirmed by live-testing each image's real lessons rather than
# guessed. Baseline (targets: Bandit/Krypton SSH boxes, the Natas LAMP
# target) covers sshd/su/chpasswd-style per-connection privilege drops,
# MPM-ITK's per-vhost setuid/setgid, and cron. SYS_CHROOT is required --
# confirmed by testing, not assumed: without it, sshd's privilege-
# separation preauth child fails outright with "chroot(\"/run/sshd\"):
# Operation not permitted", breaking every SSH connection before
# authentication even starts. NET_RAW/MKNOD from Docker's own default set
# are still left out -- neither is needed by these lessons.
_TARGET_CAPS = [
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL",
    "SETGID", "SETUID", "SETFCAP", "SETPCAP",
    "NET_BIND_SERVICE", "AUDIT_WRITE", "SYS_CHROOT",
]
# The Natas/analyst attacker workstation additionally needs raw-socket
# access for nmap/tcpdump (NET_RAW, NET_ADMIN for promiscuous capture).
_ATTACKER_CAPS = _TARGET_CAPS + ["NET_RAW", "NET_ADMIN"]


class InvalidInstanceRequestError(ValueError):
    pass


@dataclass
class InstancePlan:
    """What one (owner_id, instance_key) launch actually owns. Tearing down
    or rebooting an instance only ever affects `services` (and `network`, if
    set) — never a range's shared attacker/network."""
    type: str
    owner_id: str
    instance_key: str
    services: list[ServiceSpec]
    access: dict[str, str]
    network: "str | None" = field(default=None)  # owned + airgapped; None for target-attacker (shared range network instead)
    range_owner_id: "str | None" = field(default=None)  # set for target-attacker: which range this target belongs to


@dataclass
class RangePlan:
    """The shared, persistent-per-team half of a target-attacker range."""
    owner_id: str
    network: str
    attacker_service: ServiceSpec
    access: dict[str, str]


def _require_str(spec: dict, key: str) -> str:
    value = spec.get(key)
    if not value or not isinstance(value, str):
        raise InvalidInstanceRequestError(f"'{key}' is required and must be a non-empty string")
    return value


def _traefik_labels(router_name: str, hostname: str, port: int, challenge_network: str) -> dict[str, str]:
    return {
        "traefik.enable": "true",
        "traefik.docker.network": challenge_network,
        f"traefik.http.routers.{router_name}.rule": f"Host(`{hostname}`)",
        f"traefik.http.routers.{router_name}.entrypoints": "web,websecure",
        f"traefik.http.routers.{router_name}.tls": "true",
        f"traefik.http.services.{router_name}.loadbalancer.server.port": str(port),
    }


def plan_web_app(owner_id: str, instance_key: str, spec: dict, base_domain: str, challenge_network: str) -> InstancePlan:
    image = _require_str(spec, "image")
    port = int(spec.get("port", 3000))
    env = spec.get("env") or {}
    if not isinstance(env, dict):
        raise InvalidInstanceRequestError("'env' must be an object of string -> string")

    svc_name = naming.service_name(owner_id, instance_key)
    hostname = naming.access_hostname(owner_id, instance_key, base_domain)

    service = ServiceSpec(
        name=svc_name,
        image=image,
        networks=[challenge_network],
        labels=_traefik_labels(svc_name, hostname, port, challenge_network),
        env={str(k): str(v) for k, v in env.items()},
    )
    return InstancePlan(
        type=WEB_APP,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[service],
        access={"url": f"https://{hostname}"},
    )


def plan_single_target(owner_id: str, instance_key: str, spec: dict, allocated_port: int, base_domain: str) -> InstancePlan:
    """No Traefik involvement — a dedicated airgapped network plus a directly
    published port (e.g. for SSH). `allocated_port` is acquired by the
    caller (controller.py) from a PortAllocator, since that's inherently
    stateful and doesn't belong in this otherwise-pure planning module."""
    image = _require_str(spec, "image")
    target_port = int(spec.get("target_port", 22))
    protocol = spec.get("protocol", "ssh")
    env = spec.get("env") or {}
    if not isinstance(env, dict):
        raise InvalidInstanceRequestError("'env' must be an object of string -> string")

    svc_name = naming.service_name(owner_id, instance_key)
    net_name = naming.network_name(owner_id, instance_key)

    service = ServiceSpec(
        name=svc_name,
        image=image,
        networks=[net_name],
        env={str(k): str(v) for k, v in env.items()},
        published_port=(allocated_port, target_port),
        cap_drop=["ALL"],
        cap_add=list(_TARGET_CAPS),
    )
    return InstancePlan(
        type=SINGLE_TARGET,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[service],
        network=net_name,
        access={
            "connect_host": base_domain,
            "connect_port": allocated_port,
            "protocol": protocol,
            "note": "Connects via any swarm node — this hostname routes to whichever node the target actually landed on.",
        },
    )


def plan_range_attacker(owner_id: str, spec: dict, allocated_port: int, base_domain: str, challenge_network: str) -> RangePlan:
    """Called once per team, the first time they launch any target-attacker
    challenge. Subsequent challenges reuse this range (see plan_range_target).

    `allocated_port` publishes the attacker's SSH port directly (same
    PortAllocator pool `single-target` already draws from — see
    controller.py._create_range_target) alongside the existing Traefik/
    noVNC label-based route; ServiceSpec/docker_client.create_service()
    already support both `labels` and `published_port` on one service, so
    this doesn't touch docker_client.py at all. Without this, the
    attacker's SSH server (present in the image) was reachable from
    nowhere outside the container — confirmed by testing, not assumed."""
    attacker_image = _require_str(spec, "attacker_image")
    attacker_port = int(spec.get("attacker_port", 6080))
    attacker_ssh_port = int(spec.get("attacker_ssh_port", 22))
    attacker_env = spec.get("attacker_env") or {}

    range_network = naming.range_network_name(owner_id)
    attacker_name = naming.range_attacker_service_name(owner_id)
    hostname = naming.range_attacker_hostname(owner_id, base_domain)

    attacker_service = ServiceSpec(
        name=attacker_name,
        image=attacker_image,
        networks=[range_network, challenge_network],
        labels=_traefik_labels(attacker_name, hostname, attacker_port, challenge_network),
        env={str(k): str(v) for k, v in attacker_env.items()},
        published_port=(allocated_port, attacker_ssh_port),
        cap_drop=["ALL"],
        cap_add=list(_ATTACKER_CAPS),
    )
    return RangePlan(
        owner_id=owner_id,
        network=range_network,
        attacker_service=attacker_service,
        access={
            "attacker_url": f"https://{hostname}",
            "connect_host": base_domain,
            "connect_port": allocated_port,
            "protocol": "ssh",
            "note": "SSH connects via any swarm node — this hostname routes to whichever node the attacker actually landed on.",
        },
    )


def plan_range_target(owner_id: str, instance_key: str, spec: dict, range_network: str, range_access: dict) -> InstancePlan:
    """A single challenge's target within an existing (or about-to-exist)
    range. Joins ONLY the range's network — never challenge_network, never
    Traefik — so it is unreachable from anywhere except its range's own
    attacker."""
    target_image = _require_str(spec, "target_image")
    target_env = spec.get("target_env") or {}

    target_name = naming.range_target_service_name(owner_id, instance_key)
    target_service = ServiceSpec(
        name=target_name,
        image=target_image,
        networks=[range_network],
        env={str(k): str(v) for k, v in target_env.items()},
        cap_drop=["ALL"],
        cap_add=list(_TARGET_CAPS),
    )
    return InstancePlan(
        type=TARGET_ATTACKER,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[target_service],
        range_owner_id=owner_id,
        access={
            **range_access,
            "target_hostname": target_name,
            "target_note": "Target is reachable only from your attacker workstation, at the hostname above.",
        },
    )
