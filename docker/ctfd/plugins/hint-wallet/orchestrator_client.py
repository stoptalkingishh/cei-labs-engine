"""docker/ctfd/plugins/hint-wallet/orchestrator_client.py

Thin HTTP client for the Challenge Instance Orchestrator's hint-wallet API
(docker/orchestrator/app/wallet.py, docs/P0-FIX-LOG-2026-07-23.md). Modeled
directly on instance-launcher/orchestrator_client.py -- same base URL env
var (ORCHESTRATOR_URL, default http://orchestrator:8080, the Docker Swarm
overlay service name/port the orchestrator publishes internally -- see
docker/stack.yml, it has no published port and no Traefik route), same
plugin_shared_secret Docker secret read via read_secret(), same
X-Orchestrator-Auth header convention, same OrchestratorError contract.

Kept dependency-free of Flask/CTFd so it's trivially testable on its own,
matching instance-launcher/tests/test_orchestrator_client.py's approach.

/wallet/sync is the one method here that does NOT use X-Orchestrator-Auth
-- it's a machine-to-machine pass-through of the Wargames deploy job's own
HMAC-signed body (X-Hint-Wallet-Signature), authenticated by the
orchestrator itself against hint_wallet_sync_secret. This client's job for
that endpoint is to forward the raw bytes and the signature header
unchanged, not to re-sign or duplicate that secret material here.
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

    # ── Machine sync (proxy) ─────────────────────────────────────────────

    def proxy_wallet_sync(self, raw_body: bytes, signature: str):
        """Forwards the exact bytes and signature the machine_sync route
        received, unchanged, to POST /wallet/sync. Returns the raw
        requests.Response so the caller can relay status code/body back to
        the sender byte-for-byte -- deliberately NOT parsed/re-serialized
        here, since re-encoding JSON could change the exact bytes the
        orchestrator already validated (and the caller may want to relay a
        non-JSON error body too). Only network-level failures (connection
        refused, timeout, DNS) raise OrchestratorError; any HTTP status the
        orchestrator itself returns is handed back as-is, not raised.
        """
        try:
            return requests.post(
                f"{self.base_url}/wallet/sync",
                data=raw_body,
                headers={"X-Hint-Wallet-Signature": signature, "Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise OrchestratorError(f"orchestrator unreachable: {exc}") from exc

    # ── Player-facing wallet actions ─────────────────────────────────────

    def balance(self, owner_id: str) -> dict:
        resp = requests.get(f"{self.base_url}/wallet/balance/{owner_id}", headers=self._headers(), timeout=self.timeout)
        if resp.status_code != 200:
            self._raise_for_error(resp)
        return resp.json()

    def deduct(self, owner_id: str, track: str, entry_name: str, tier: int) -> dict:
        """Spends a hint tier's cost for owner_id. Unlike most of this
        client's methods, a non-200 status here is an ordinary, expected
        outcome (insufficient balance, unknown hint, no catalog yet) --
        not an exceptional one -- so it's returned as a normal dict with
        success=False and the orchestrator's exact status_code/body rather
        than raised, letting the caller (routes.py's /api/unlock) relay the
        precise error and status code to the player instead of collapsing
        every failure into one generic message. Only a genuine transport
        failure (orchestrator unreachable) raises OrchestratorError."""
        try:
            resp = requests.post(
                f"{self.base_url}/wallet/deduct",
                json={"owner_id": owner_id, "track": track, "entry_name": entry_name, "tier": tier},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise OrchestratorError(f"orchestrator unreachable: {exc}") from exc

        try:
            body = resp.json()
        except ValueError:
            body = {"error": resp.text}

        if resp.status_code == 200:
            return {"success": True, **body}
        return {"success": False, "status_code": resp.status_code, **body}
