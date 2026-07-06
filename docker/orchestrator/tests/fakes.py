"""Test double for DockerOrchestratorClient — records calls, touches no daemon."""


class FakeDockerOrchestratorClient:
    def __init__(self):
        self.services: dict[str, object] = {}
        self.networks: set[str] = set()
        self.create_calls = []
        self.remove_service_calls = []
        self.remove_network_calls = []

    def ensure_team_network(self, name: str) -> None:
        self.networks.add(name)

    def remove_network(self, name: str) -> None:
        self.remove_network_calls.append(name)
        self.networks.discard(name)

    def get_service(self, name: str):
        return self.services.get(name)

    def create_service(self, spec):
        self.create_calls.append(spec)
        self.services[spec.name] = spec
        return spec

    def remove_service(self, name: str) -> None:
        self.remove_service_calls.append(name)
        self.services.pop(name, None)

    def list_managed_services(self):
        return list(self.services.values())
