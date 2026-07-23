"""Tests routes.py's blueprint end-to-end via a real Flask test client.

Stubs CTFd/Flask-adjacent internals routes.py imports at module level (none
of which are installed outside a real CTFd deployment) with plain Python
doubles, following the exact pattern
instance-launcher/tests/test_secret_scrubbing.py already uses in this repo.
Real `flask` and `requests` ARE installed (both are dependencies of
orchestrator_client.py / the orchestrator itself), so those aren't stubbed
-- routes.py's blueprint is exercised through an actual Flask app and
test_client(), not just called as bare functions, so its route decorators,
URL rules, and status codes are all genuinely covered.
"""
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask


# ── Fake CTFd.models / HintWalletCatalog table ──────────────────────────────

class _FakeCatalogTable:
    row = None

    @classmethod
    def reset(cls):
        cls.row = None


class _FakeCatalogQuery:
    @staticmethod
    def get(_id):
        return _FakeCatalogTable.row


class HintWalletCatalog:
    query = _FakeCatalogQuery

    def __init__(self, id=None, bundle_json="{}"):
        self.id = id
        self.bundle_json = bundle_json
        self.revision = None


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)
        _FakeCatalogTable.row = obj

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


class _FakeDb:
    session = _FakeSession()

    class Model:
        pass

    class Column:
        def __init__(self, *a, **kw):
            pass


def _install_stubs():
    ctfd = types.ModuleType("CTFd")
    ctfd_models = types.ModuleType("CTFd.models")
    ctfd_models.db = _FakeDb

    ctfd_plugins = types.ModuleType("CTFd.plugins")
    ctfd_plugins.bypass_csrf_protection = lambda f: f

    ctfd_utils = types.ModuleType("CTFd.utils")
    ctfd_decorators = types.ModuleType("CTFd.utils.decorators")
    ctfd_decorators.authed_only = lambda f: f
    ctfd_user = types.ModuleType("CTFd.utils.user")
    ctfd_user.get_current_user = lambda: SimpleNamespace(account_id=1)

    plugin_package = types.ModuleType("hint_wallet")
    plugin_package.__path__ = []

    models_mod = types.ModuleType("hint_wallet.models")
    models_mod.HintWalletCatalog = HintWalletCatalog

    orch_client_mod = types.ModuleType("hint_wallet.orchestrator_client")

    class _PlaceholderClient:
        @classmethod
        def from_env(cls):
            raise AssertionError("test must monkeypatch routes.OrchestratorClient before calling a route")

    class _OrchestratorError(Exception):
        pass

    orch_client_mod.OrchestratorClient = _PlaceholderClient
    orch_client_mod.OrchestratorError = _OrchestratorError

    sys.modules.update({
        "CTFd": ctfd,
        "CTFd.models": ctfd_models,
        "CTFd.plugins": ctfd_plugins,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.decorators": ctfd_decorators,
        "CTFd.utils.user": ctfd_user,
        "hint_wallet": plugin_package,
        "hint_wallet.models": models_mod,
        "hint_wallet.orchestrator_client": orch_client_mod,
    })
    return orch_client_mod.OrchestratorError


OrchestratorError = _install_stubs()

module_path = Path(__file__).resolve().parents[1] / "routes.py"
spec = importlib.util.spec_from_file_location("hint_wallet.routes", module_path)
routes = importlib.util.module_from_spec(spec)
sys.modules["hint_wallet.routes"] = routes
spec.loader.exec_module(routes)


@pytest.fixture
def app_client():
    app = Flask(__name__)
    app.register_blueprint(routes.hint_wallet_bp)
    _FakeCatalogTable.reset()
    routes.db = _FakeDb
    routes.db.session = _FakeSession()
    return app.test_client()


def _fake_orch_response(status_code, body: dict):
    resp = Mock()
    resp.status_code = status_code
    resp.content = json.dumps(body).encode()
    resp.headers = {"Content-Type": "application/json"}
    return resp


def _install_fake_client(monkeypatch, **method_impls):
    class _FakeClient:
        def __init__(self):
            for name, impl in method_impls.items():
                setattr(self, name, impl)

    class _FakeClientFactory:
        instances = []

        @classmethod
        def from_env(cls):
            inst = _FakeClient()
            cls.instances.append(inst)
            return inst

    monkeypatch.setattr(routes, "OrchestratorClient", _FakeClientFactory)
    return _FakeClientFactory


# ── /machine/sync ────────────────────────────────────────────────────────

def test_machine_sync_missing_signature_rejected_without_calling_orchestrator(app_client, monkeypatch):
    called = []
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: called.append(1) or _fake_orch_response(200, {}))

    resp = app_client.post("/plugins/hint-wallet/machine/sync", data=b'{"a":1}', content_type="application/json")

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_signature"
    assert called == [], "orchestrator must not be called when the signature header is missing (fail fast)"


def test_machine_sync_empty_body_rejected_without_calling_orchestrator(app_client, monkeypatch):
    called = []
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: called.append(1) or _fake_orch_response(200, {}))

    resp = app_client.post(
        "/plugins/hint-wallet/machine/sync", data=b"", headers={"X-Hint-Wallet-Signature": "sig"}
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_schema"
    assert called == [], "orchestrator must not be called for an empty body (fail fast)"


def test_machine_sync_forwards_body_and_signature_unchanged(app_client, monkeypatch):
    captured = {}
    raw_body = json.dumps({"schema_version": 1, "revision": 3, "manifests": []}).encode()

    def fake_proxy(body, signature):
        captured["body"] = body
        captured["signature"] = signature
        return _fake_orch_response(200, {"status": "accepted", "revision": 3})

    _install_fake_client(monkeypatch, proxy_wallet_sync=fake_proxy)

    resp = app_client.post(
        "/plugins/hint-wallet/machine/sync",
        data=raw_body,
        headers={"X-Hint-Wallet-Signature": "the-signature"},
        content_type="application/json",
    )

    assert captured["body"] == raw_body
    assert captured["signature"] == "the-signature"
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


@pytest.mark.parametrize(
    "orch_status,orch_body",
    [
        (401, {"error": "invalid_signature"}),
        (400, {"error": "invalid_schema"}),
        (409, {"error": "stale_revision"}),
        (422, {"error": "catalog_validation_failed"}),
        (503, {"error": "secret_or_database_unavailable"}),
    ],
)
def test_machine_sync_relays_orchestrator_error_status_unchanged(app_client, monkeypatch, orch_status, orch_body):
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: _fake_orch_response(orch_status, orch_body))

    resp = app_client.post(
        "/plugins/hint-wallet/machine/sync",
        data=b'{"schema_version":1,"revision":1,"manifests":[]}',
        headers={"X-Hint-Wallet-Signature": "sig"},
        content_type="application/json",
    )

    assert resp.status_code == orch_status
    assert resp.get_json() == orch_body


def test_machine_sync_caches_catalog_locally_only_on_orchestrator_acceptance(app_client, monkeypatch):
    bundle = {"schema_version": 1, "revision": 5, "manifests": [{"track": "bandit", "entries": []}]}
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: _fake_orch_response(200, {"status": "accepted"}))

    app_client.post(
        "/plugins/hint-wallet/machine/sync",
        data=json.dumps(bundle).encode(),
        headers={"X-Hint-Wallet-Signature": "sig"},
        content_type="application/json",
    )

    assert _FakeCatalogTable.row is not None
    assert json.loads(_FakeCatalogTable.row.bundle_json)["revision"] == 5


def test_machine_sync_does_not_cache_on_orchestrator_rejection(app_client, monkeypatch):
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: _fake_orch_response(422, {"error": "catalog_validation_failed"}))

    app_client.post(
        "/plugins/hint-wallet/machine/sync",
        data=b'{"schema_version":1,"revision":1,"manifests":[]}',
        headers={"X-Hint-Wallet-Signature": "sig"},
        content_type="application/json",
    )

    assert _FakeCatalogTable.row is None


# ── /api/balance ─────────────────────────────────────────────────────────

def test_api_balance_forwards_authenticated_owner_id(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=42))
    captured = {}

    def fake_balance(owner_id):
        captured["owner_id"] = owner_id
        return {"owner_id": owner_id, "balance": 100}

    _install_fake_client(monkeypatch, balance=fake_balance)

    resp = app_client.get("/plugins/hint-wallet/api/balance")

    assert resp.status_code == 200
    assert captured["owner_id"] == "42"
    assert resp.get_json()["balance"] == 100


# ── /api/unlock ──────────────────────────────────────────────────────────

def test_api_unlock_forwards_owner_id_and_hint_selection(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    captured = {}

    def fake_deduct(owner_id, track, entry_name, tier):
        captured.update(owner_id=owner_id, track=track, entry_name=entry_name, tier=tier)
        return {"success": True, "status": "unlocked", "balance": 20, "cost": 10, "content": "use ssh -h"}

    _install_fake_client(monkeypatch, deduct=fake_deduct)

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "Bandit 0 -> 1", "tier": 1},
    )

    assert resp.status_code == 200
    assert captured == {"owner_id": "7", "track": "bandit", "entry_name": "Bandit 0 -> 1", "tier": 1}
    assert resp.get_json()["content"] == "use ssh -h"


def test_api_unlock_insufficient_balance_propagates_as_error_not_silent_success(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    _install_fake_client(
        monkeypatch,
        deduct=lambda *a, **kw: {"success": False, "status_code": 402, "error": "insufficient_balance", "balance": 5, "cost": 10},
    )

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "Bandit 0 -> 1", "tier": 3},
    )

    assert resp.status_code == 402, "an orchestrator rejection must not come back as a 200"
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == "insufficient_balance"


def test_api_unlock_missing_fields_rejected_before_calling_orchestrator(app_client, monkeypatch):
    called = []
    _install_fake_client(monkeypatch, deduct=lambda *a, **kw: called.append(1))

    resp = app_client.post("/plugins/hint-wallet/api/unlock", json={"track": "bandit"})

    assert resp.status_code == 400
    assert called == []


# ── /api/tiers ───────────────────────────────────────────────────────────

def test_api_tiers_lists_costs_without_exposing_content(app_client):
    _FakeCatalogTable.row = HintWalletCatalog(
        id=1,
        bundle_json=json.dumps({
            "manifests": [
                {
                    "track": "bandit",
                    "entries": [
                        {
                            "name": "Bandit 0 -> 1",
                            "tiers": [
                                {"tier": 1, "cost": 10, "content": "secret nudge"},
                                {"tier": 2, "cost": 20, "content": "secret nudge 2"},
                            ],
                        }
                    ],
                }
            ]
        }),
    )

    resp = app_client.get("/plugins/hint-wallet/api/tiers/bandit/Bandit 0 -> 1")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tiers"] == [{"tier": 1, "cost": 10}, {"tier": 2, "cost": 20}]
    assert "content" not in json.dumps(body)


def test_api_tiers_no_active_catalog(app_client):
    resp = app_client.get("/plugins/hint-wallet/api/tiers/bandit/Bandit 0 -> 1")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_active_catalog"
