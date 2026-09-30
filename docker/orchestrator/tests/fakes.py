"""Test double for DockerOrchestratorClient — records calls, touches no daemon."""


import time
from datetime import datetime, timezone
from types import SimpleNamespace


def _rfc3339(timestamp):
    """Docker reports service/network creation times as RFC3339 strings in
    `attrs` (see reaper._managed_resource_age_seconds), so the fake has to
    produce the same shape -- anything hand-rolled here would let the
    reaper's "too young to be an orphan" guard pass vacuously."""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class FakeDockerOrchestratorClient:
    def __init__(self):
        self.services: dict[str, object] = {}
        self.networks: dict[str, bool] = {}  # name -> internal
        self.create_calls = []
        self.remove_service_calls = []
        self.remove_network_calls = []
        self.restart_calls = []
        # name -> epoch seconds the resource was created. Registered by
        # create_service()/ensure_network(); tests that inject a resource
        # directly can pre-seed it to make the resource old or new.
        self.resource_created_at: dict[str, float] = {}

    def ensure_network(self, name: str, internal: bool = True) -> None:
        self.networks[name] = internal
        self.resource_created_at.setdefault(name, time.time())

    def remove_network(self, name: str) -> None:
        self.remove_network_calls.append(name)
        self.networks.pop(name, None)

    def get_service(self, name: str):
        return self.services.get(name)

    def create_service(self, spec):
        self.create_calls.append(spec)
        self.services[spec.name] = spec
        self.resource_created_at.setdefault(spec.name, time.time())
        return spec

    def remove_service(self, name: str) -> None:
        self.remove_service_calls.append(name)
        self.services.pop(name, None)

    def restart_service(self, name: str) -> bool:
        self.restart_calls.append(name)
        return name in self.services

    def list_managed_services(self):
        return [
            SimpleNamespace(
                name=spec.name,
                published_ports=spec.published_ports,
                attrs={"CreatedAt": _rfc3339(self.resource_created_at.get(spec.name))},
            )
            for spec in self.services.values()
        ]

    def list_managed_networks(self):
        return [
            SimpleNamespace(name=name, attrs={"Created": _rfc3339(self.resource_created_at.get(name))})
            for name in self.networks
        ]
