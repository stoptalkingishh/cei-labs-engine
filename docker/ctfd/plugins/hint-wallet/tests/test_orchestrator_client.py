"""Tests orchestrator_client.py in isolation, without importing the plugin
package's __init__.py (which pulls in CTFd, not installed in this test env)
-- same technique as instance-launcher/tests/test_orchestrator_client.py.
"""
import importlib.util
import os
import sys
from unittest.mock import Mock, patch

import pytest
import requests

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "orchestrator_client.py")
spec = importlib.util.spec_from_file_location("hint_wallet_orchestrator_client", MODULE_PATH)
orchestrator_client = importlib.util.module_from_spec(spec)
sys.modules["hint_wallet_orchestrator_client"] = orchestrator_client
spec.loader.exec_module(orchestrator_client)

OrchestratorClient = orchestrator_client.OrchestratorClient
OrchestratorError = orchestrator_client.OrchestratorError


def make_response(status_code, json_body=None, content=b""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = str(json_body)
    resp.content = content
    resp.headers = {"Content-Type": "application/json"}
    return resp


def client():
    return OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")


# ── proxy_wallet_sync ────────────────────────────────────────────────────

def test_proxy_wallet_sync_forwards_raw_body_and_signature_unchanged():
    raw_body = b'{"schema_version":1,"revision":7,"manifests":[]}'
    with patch("hint_wallet_orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(200, {"status": "accepted"})
        client().proxy_wallet_sync(raw_body, "abc123signature")

    args, kwargs = mock_post.call_args
    assert args[0] == "http://orchestrator:8080/wallet/sync"
    assert kwargs["data"] == raw_body
    assert kwargs["headers"]["X-Hint-Wallet-Signature"] == "abc123signature"
    # No X-Orchestrator-Auth here -- /wallet/sync uses a different trust
    # boundary (the signature), not the plugin_shared_secret.
    assert "X-Orchestrator-Auth" not in kwargs["headers"]


@pytest.mark.parametrize("status_code", [200, 400, 401, 409, 422, 503])
def test_proxy_wallet_sync_relays_every_orchestrator_status_code_unmodified(status_code):
    with patch("hint_wallet_orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(status_code, {"error": "whatever"})
        resp = client().proxy_wallet_sync(b"{}", "sig")
    assert resp.status_code == status_code


def test_proxy_wallet_sync_raises_orchestrator_error_on_network_failure():
    with patch("hint_wallet_orchestrator_client.requests.post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(OrchestratorError, match="unreachable"):
            client().proxy_wallet_sync(b"{}", "sig")


# ── balance ──────────────────────────────────────────────────────────────

def test_balance_sends_expected_request_and_returns_body():
    with patch("hint_wallet_orchestrator_client.requests.get") as mock_get:
        mock_get.return_value = make_response(200, {"owner_id": "team-1", "balance": 40})
        result = client().balance("team-1")

    args, kwargs = mock_get.call_args
    assert args[0] == "http://orchestrator:8080/wallet/balance/team-1"
    assert kwargs["headers"]["X-Orchestrator-Auth"] == "s3cr3t"
    assert result == {"owner_id": "team-1", "balance": 40}


def test_balance_raises_on_error_status():
    with patch("hint_wallet_orchestrator_client.requests.get") as mock_get:
        mock_get.return_value = make_response(500, {"error": "boom"})
        with pytest.raises(OrchestratorError, match="boom"):
            client().balance("team-1")


# ── deduct ───────────────────────────────────────────────────────────────

def test_deduct_sends_expected_payload_and_headers():
    with patch("hint_wallet_orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(200, {"status": "unlocked", "balance": 30, "cost": 10, "content": "hint text"})
        result = client().deduct("team-1", "bandit", "Bandit 0 -> 1", 1)

    args, kwargs = mock_post.call_args
    assert args[0] == "http://orchestrator:8080/wallet/deduct"
    assert kwargs["json"] == {"owner_id": "team-1", "track": "bandit", "entry_name": "Bandit 0 -> 1", "tier": 1}
    assert kwargs["headers"]["X-Orchestrator-Auth"] == "s3cr3t"
    assert result["success"] is True
    assert result["content"] == "hint text"


def test_deduct_insufficient_balance_returns_error_dict_not_success():
    with patch("hint_wallet_orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(402, {"error": "insufficient_balance", "balance": 5, "cost": 10})
        result = client().deduct("team-1", "bandit", "Bandit 0 -> 1", 1)

    assert result["success"] is False
    assert result["status_code"] == 402
    assert result["error"] == "insufficient_balance"
    assert result["balance"] == 5


def test_deduct_hint_not_found_propagates_as_error():
    with patch("hint_wallet_orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(404, {"error": "hint_not_found"})
        result = client().deduct("team-1", "bandit", "does-not-exist", 1)

    assert result["success"] is False
    assert result["status_code"] == 404


def test_deduct_raises_orchestrator_error_on_network_failure():
    with patch("hint_wallet_orchestrator_client.requests.post", side_effect=requests.Timeout("slow")):
        with pytest.raises(OrchestratorError, match="unreachable"):
            client().deduct("team-1", "bandit", "x", 1)
