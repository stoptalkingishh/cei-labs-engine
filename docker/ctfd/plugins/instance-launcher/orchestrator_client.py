"""docker/ctfd/plugins/instance-launcher/orchestrator_client.py

Thin HTTP client for the Challenge Instance Orchestrator's internal API
(docker/orchestrator/README.md). Kept dependency-free of Flask/CTFd so it's
trivially testable on its own.
"""
import os

import requests


class OrchestratorError(Exception):
    pass


def read_secret(name: str) -> str:
    path = f"/run/secrets/{name}"
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name.upper(), "")


class OrchestratorClient:
    def __init__(self, base_url: str, shared_secret: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.shared_secret = shared_secret
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OrchestratorClient":
        return cls(
            base_url=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8080"),
            shared_secret=read_secret("plugin_shared_secret"),
        )

    def _headers(self) -> dict:
        return {"X-Orchestrator-Auth": self.shared_secret, "Content-Type": "application/json"}

    def _raise_for_error(self, resp) -> None:
        try:
            message = resp.json().get("error", resp.text)
        except ValueError:
            message = resp.text
        raise OrchestratorError(f"orchestrator returned {resp.status_code}: {message}")

    def create_or_get(self, instance_type: str, owner_id: str, instance_key: str, spec: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}/instances",
            json={"type": instance_type, "owner_id": owner_id, "instance_key": instance_key, "spec": spec},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 201):
            self._raise_for_error(resp)
        return resp.json()

    def get(self, owner_id: str, instance_key: str) -> "dict | None":
        resp = requests.get(
            f"{self.base_url}/instances/{owner_id}/{instance_key}", headers=self._headers(), timeout=self.timeout
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            self._raise_for_error(resp)
        return resp.json()

    def delete(self, owner_id: str, instance_key: str) -> bool:
        resp = requests.delete(
            f"{self.base_url}/instances/{owner_id}/{instance_key}", headers=self._headers(), timeout=self.timeout
        )
        if resp.status_code not in (200, 404):
            self._raise_for_error(resp)
        return True
