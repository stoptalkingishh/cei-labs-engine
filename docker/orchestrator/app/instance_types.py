"""docker/orchestrator/app/instance_types.py

Pure planning logic: given a request body, decide what Docker services (and,
for target-attacker, what throwaway network) are needed. No Docker API calls
happen here, which is what makes this module unit-testable without a daemon.

Three instance types, per the migration plan:
  - web-app:          one container, HTTP-routed through Traefik.
                       Covers Juice Shop today.
  - single-target:    one container, HTTP-routed through Traefik.
                       Same mechanics as web-app; kept distinct in the
                       registry for standalone shell/service challenges that
                       aren't paired with an attacker box.
  - target-attacker:  two containers (target + attacker) on a fresh per-team
                       overlay network. Only the attacker (browser noVNC) is
                       reachable via Traefik; the target is never reachable
                       from anything except its own paired attacker.
"""
from dataclasses import dataclass, field

from .docker_client import ServiceSpec
from . import naming

WEB_APP = "web-app"
SINGLE_TARGET = "single-target"
TARGET_ATTACKER = "target-attacker"

VALID_TYPES = (WEB_APP, SINGLE_TARGET, TARGET_ATTACKER)


class InvalidInstanceRequestError(ValueError):
    pass


@dataclass
class InstancePlan:
    type: str
    owner_id: str
    instance_key: str
    services: list[ServiceSpec]
    access: dict[str, str]
    team_network: "str | None" = field(default=None)


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


def _plan_single_container(
    instance_type: str, owner_id: str, instance_key: str, spec: dict, base_domain: str, challenge_network: str
) -> InstancePlan:
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
        type=instance_type,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[service],
        access={"url": f"https://{hostname}"},
    )


def _plan_target_attacker(
    owner_id: str, instance_key: str, spec: dict, base_domain: str, challenge_network: str
) -> InstancePlan:
    target_image = _require_str(spec, "target_image")
    attacker_image = _require_str(spec, "attacker_image")
    target_env = spec.get("target_env") or {}
    attacker_env = spec.get("attacker_env") or {}
    attacker_port = int(spec.get("attacker_port", 6080))

    team_network = naming.network_name(owner_id, instance_key)
    target_name = naming.service_name(owner_id, instance_key, "target")
    attacker_name = naming.service_name(owner_id, instance_key, "attacker")
    hostname = naming.access_hostname(owner_id, instance_key, base_domain)

    # Target only ever joins the private per-team network — never
    # challenge_network, so it is unreachable from Traefik, other teams, or
    # anything besides its own paired attacker.
    target_service = ServiceSpec(
        name=target_name,
        image=target_image,
        networks=[team_network],
        env={str(k): str(v) for k, v in target_env.items()},
    )
    attacker_service = ServiceSpec(
        name=attacker_name,
        image=attacker_image,
        networks=[team_network, challenge_network],
        labels=_traefik_labels(attacker_name, hostname, attacker_port, challenge_network),
        env={str(k): str(v) for k, v in attacker_env.items()},
    )

    return InstancePlan(
        type=TARGET_ATTACKER,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[target_service, attacker_service],
        access={"attacker_url": f"https://{hostname}"},
        team_network=team_network,
    )


def plan(instance_type: str, owner_id: str, instance_key: str, spec: dict, base_domain: str, challenge_network: str) -> InstancePlan:
    if instance_type in (WEB_APP, SINGLE_TARGET):
        return _plan_single_container(instance_type, owner_id, instance_key, spec, base_domain, challenge_network)
    if instance_type == TARGET_ATTACKER:
        return _plan_target_attacker(owner_id, instance_key, spec, base_domain, challenge_network)
    raise InvalidInstanceRequestError(f"unknown instance type {instance_type!r}, must be one of {VALID_TYPES}")
