"""Test double for DockerOrchestratorClient — records calls, touches no daemon."""


from types import SimpleNamespace


class FakeDockerOrchestratorClient:
    def __init__(self):
        self.services: dict[str, object] = {}
        self.networks: dict[str, bool] = {}  # name -> internal
        self.create_calls = []
        self.remove_service_calls = []
        self.remove_network_calls = []
        self.restart_calls = []

    def ensure_network(self, name: str, internal: bool = True) -> None:
        self.networks[name] = internal

    def remove_network(self, name: str) -> None:
        self.remove_network_calls.append(name)
        self.networks.pop(name, None)

    def get_service(self, name: str):
        return self.services.get(name)

    def create_service(self, spec):
        self.create_calls.append(spec)
        self.services[spec.name] = spec
        return spec

    def remove_service(self, name: str) -> None:
        self.remove_service_calls.append(name)
        self.services.pop(name, None)

    def restart_service(self, name: str) -> bool:
        self.restart_calls.append(name)
        return name in self.services

    def list_managed_services(self):
        return list(self.services.values())

    def list_managed_networks(self):
        return [SimpleNamespace(name=name) for name in self.networks]
