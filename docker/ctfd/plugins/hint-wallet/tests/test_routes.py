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


# ── Fake CTFd.models.Challenges / Solves (progression-window gating) ───────

class _FakeChallengeRow:
    def __init__(self, id, name, category):
        self.id = id
        self.name = name
        self.category = category


class _FakeSolveRow:
    def __init__(self, account_id, challenge_id):
        self.account_id = account_id
        self.challenge_id = challenge_id


class _FakeQuery:
    """Minimal stand-in for a SQLAlchemy Query supporting the subset
    routes.py actually calls: .filter(...)/.filter_by(...) with predicate
    functions built by _Column below, plus .order_by(...).all()/.first().
    Deliberately simple (no real SQL) -- same house style as
    instance-launcher/tests' hand-rolled fakes."""

    def __init__(self, rows, predicates=None):
        self._rows = rows
        self._predicates = predicates or []

    def filter(self, *predicates):
        return _FakeQuery(self._rows, self._predicates + list(predicates))

    def filter_by(self, **kwargs):
        preds = [(lambda row, k=k, v=v: getattr(row, k, None) == v) for k, v in kwargs.items()]
        return _FakeQuery(self._rows, self._predicates + preds)

    def order_by(self, *_a, **_kw):
        return self

    def _matched(self):
        return [row for row in self._rows if all(p(row) for p in self._predicates)]

    def all(self):
        rows = self._matched()
        if all(hasattr(r, "id") for r in rows):
            rows = sorted(rows, key=lambda r: r.id)
        return rows

    def first(self):
        matched = self._matched()
        return matched[0] if matched else None


class _Column:
    """Stands in for a SQLAlchemy InstrumentedAttribute -- just enough of
    its comparison-operator protocol for routes.py's own usage
    (`Column == value`, `Column.in_(values)`, `Column.asc()`,
    `db.func.lower(Column) == value`) to build a row-predicate function
    instead of a real SQL clause."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return lambda row: getattr(row, self.name, None) == other

    def in_(self, values):
        values = set(values)
        return lambda row: getattr(row, self.name, None) in values

    def asc(self):
        return self


class _LowerFn:
    def __init__(self, column: _Column):
        self._column = column

    def __eq__(self, other):
        return lambda row: str(getattr(row, self._column.name, "")).lower() == other


class Challenges:
    id = _Column("id")
    name = _Column("name")
    category = _Column("category")
    query = None  # set per-test via _install_fake_data


class Solves:
    account_id = _Column("account_id")
    challenge_id = _Column("challenge_id")
    query = None


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


class _FakeFunc:
    @staticmethod
    def lower(column):
        return _LowerFn(column)


class _FakeDb:
    session = _FakeSession()
    func = _FakeFunc

    class Model:
        pass

    class Column:
        def __init__(self, *a, **kw):
            pass


def _install_stubs():
    ctfd = types.ModuleType("CTFd")
    ctfd_models = types.ModuleType("CTFd.models")
    ctfd_models.db = _FakeDb
    ctfd_models.Challenges = Challenges
    ctfd_models.Solves = Solves

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

# progression.py and track_mapping.py have zero CTFd imports (see their own
# header comments), so they're loaded for real -- routes.py's
# `from .progression import is_unlockable` / `from .track_mapping import
# category_for_track` need the genuine modules registered under the
# package name, not stubs.
def _load_real_submodule(name):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"hint_wallet.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"hint_wallet.{name}"] = module
    spec.loader.exec_module(module)
    return module


progression = _load_real_submodule("progression")
track_mapping = _load_real_submodule("track_mapping")

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
    Challenges.query = _FakeQuery([])
    Solves.query = _FakeQuery([])
    return app.test_client()


def _set_track_challenges(*rows):
    """rows: _FakeChallengeRow(id, name, category) tuples, this track's
    challenges in sequence order (mirrors what a real deployment's
    Challenges table looks like after the Wargames builders create levels
    0, 1, 2, ... in ascending id order)."""
    Challenges.query = _FakeQuery(list(rows))


def _set_solves(*rows):
    """rows: _FakeSolveRow(account_id, challenge_id) tuples."""
    Solves.query = _FakeQuery(list(rows))


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


def test_machine_sync_never_persists_hint_content_in_local_cache(app_client, monkeypatch):
    """The cache exists only so /api/tiers can list tier numbers/costs
    pre-spend -- api_tiers() already strips `content` at serialization
    time, but that alone isn't enough: if the raw content were also
    written to bundle_json, it would sit unencrypted in CTFd's own
    database, readable by anything with DB access (SQLi, admin-panel
    dump, backup) even though no current route serializes it back out.
    The invariant has to hold at the point of writing the cache, not
    just at the one read call site."""
    bundle = {
        "schema_version": 1,
        "revision": 9,
        "manifests": [
            {
                "track": "bandit",
                "entries": [
                    {
                        "name": "Bandit 0 -> 1",
                        "tiers": [
                            {"tier": 1, "cost": 10, "content": "secret nudge"},
                            {"tier": 2, "cost": 20, "content": "bigger secret nudge"},
                        ],
                    }
                ],
            }
        ],
    }
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: _fake_orch_response(200, {"status": "accepted"}))

    app_client.post(
        "/plugins/hint-wallet/machine/sync",
        data=json.dumps(bundle).encode(),
        headers={"X-Hint-Wallet-Signature": "sig"},
        content_type="application/json",
    )

    assert _FakeCatalogTable.row is not None
    persisted = json.loads(_FakeCatalogTable.row.bundle_json)
    for manifest in persisted["manifests"]:
        for entry in manifest["entries"]:
            for tier in entry["tiers"]:
                assert "content" not in tier, f"hint content leaked into local cache: {tier}"
                assert set(tier.keys()) == {"tier", "cost"}


def test_machine_sync_does_not_cache_on_orchestrator_rejection(app_client, monkeypatch):
    _install_fake_client(monkeypatch, proxy_wallet_sync=lambda *a, **kw: _fake_orch_response(422, {"error": "catalog_validation_failed"}))

    app_client.post(
        "/plugins/hint-wallet/machine/sync",
        data=b'{"schema_version":1,"revision":1,"manifests":[]}',
        headers={"X-Hint-Wallet-Signature": "sig"},
        content_type="application/json",
    )

    assert _FakeCatalogTable.row is None


# ── /api/unlock ──────────────────────────────────────────────────────────
# No /api/balance anymore (cei-labs-event#7 -- no shared team-currency
# wallet exists in this system). /api/unlock now also enforces the
# progression-window gate (progression.py) before ever calling the
# orchestrator, on top of forwarding the hint selection.

def test_api_unlock_forwards_owner_id_and_hint_selection_when_in_window(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    _set_track_challenges(_FakeChallengeRow(1, "Bandit 0 -> 1", "Linux Basics"))
    _set_solves()  # nothing solved yet -> challenge 1 is the fresh window
    captured = {}

    def fake_unlock(owner_id, track, entry_name, tier):
        captured.update(owner_id=owner_id, track=track, entry_name=entry_name, tier=tier)
        return {"success": True, "status": "unlocked", "cost_percent": 10, "content": "use ssh -h"}

    _install_fake_client(monkeypatch, unlock=fake_unlock)

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "Bandit 0 -> 1", "tier": 1},
    )

    assert resp.status_code == 200
    assert captured == {"owner_id": "7", "track": "bandit", "entry_name": "Bandit 0 -> 1", "tier": 1}
    assert resp.get_json()["content"] == "use ssh -h"


def test_api_unlock_outside_progression_window_is_409_without_calling_orchestrator(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    _set_track_challenges(
        _FakeChallengeRow(1, "Bandit 0 -> 1", "Linux Basics"),
        _FakeChallengeRow(2, "Bandit 1 -> 2", "Linux Basics"),
        _FakeChallengeRow(40, "Bandit 39 -> 40", "Linux Basics"),
    )
    _set_solves()  # nothing solved -- window is {1, 2}, challenge 40 is locked
    called = []
    _install_fake_client(monkeypatch, unlock=lambda *a, **kw: called.append(1))

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "Bandit 39 -> 40", "tier": 1},
    )

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "progression_locked"
    assert called == [], "the orchestrator must never be called for a progression-locked hint"


def test_api_unlock_window_shifts_as_challenges_are_solved(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    _set_track_challenges(
        *[_FakeChallengeRow(i, f"Bandit {i}", "Linux Basics") for i in range(1, 13)]
    )
    # This owner has solved challenges 1-10 -- window is now {11, 12}.
    _set_solves(*[_FakeSolveRow(7, i) for i in range(1, 11)])
    _install_fake_client(monkeypatch, unlock=lambda *a, **kw: {"success": True, "status": "unlocked", "cost_percent": 10, "content": "hi"})

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "Bandit 11", "tier": 1},
    )
    assert resp.status_code == 200

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "Bandit 1", "tier": 1},
    )
    assert resp.status_code == 409, "already-solved-and-passed challenge 1 must not be unlockable anymore"


def test_api_unlock_progression_window_is_independent_per_track(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    _set_track_challenges(
        _FakeChallengeRow(1, "Bandit 0 -> 1", "Linux Basics"),
        _FakeChallengeRow(2, "Bandit 1 -> 2", "Linux Basics"),
        _FakeChallengeRow(101, "Krypton 0 -> 1", "Cryptography"),
        _FakeChallengeRow(102, "Krypton 1 -> 2", "Cryptography"),
    )
    # Bandit fully solved, Krypton untouched -- Krypton's own window must
    # still be its own first two challenges regardless of Bandit progress.
    _set_solves(_FakeSolveRow(7, 1), _FakeSolveRow(7, 2))
    _install_fake_client(monkeypatch, unlock=lambda *a, **kw: {"success": True, "status": "unlocked", "cost_percent": 10, "content": "hi"})

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "krypton", "entry_name": "Krypton 0 -> 1", "tier": 1},
    )
    assert resp.status_code == 200


def test_api_unlock_for_a_challenge_ctfd_does_not_know_about_is_404(app_client, monkeypatch):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=7))
    _set_track_challenges()  # empty -- track has no challenges in CTFd at all
    called = []
    _install_fake_client(monkeypatch, unlock=lambda *a, **kw: called.append(1))

    resp = app_client.post(
        "/plugins/hint-wallet/api/unlock",
        json={"track": "bandit", "entry_name": "does not exist", "tier": 1},
    )

    assert resp.status_code == 404
    assert called == []


def test_api_unlock_missing_fields_rejected_before_progression_check_or_orchestrator(app_client, monkeypatch):
    called = []
    _install_fake_client(monkeypatch, unlock=lambda *a, **kw: called.append(1))

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
