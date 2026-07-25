"""Tests solve_hook.py's after_request hook end-to-end against a real Flask
app + test_client (not bare function calls), following the same
CTFd-stubbing technique instance-launcher/tests/test_secret_scrubbing.py and
this plugin's own test_routes.py already use -- CTFd isn't installed in
this test environment.

Covers cei-labs-event#7's score-reduction contract: a correct solve applies
a compensating negative Award sized to the tier the owner had opened for
THAT specific challenge (percent of the challenge's own value), is a no-op
when no hint was opened, is idempotent against a duplicate "correct"
observation, and never raises even when the orchestrator is unreachable.
"""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request


# ── Fake CTFd.models (Awards / Challenges / db) ─────────────────────────────

class _FakeChallenge:
    def __init__(self, id, name, category, value):
        self.id = id
        self.name = name
        self.category = category
        self.value = value


class _FakeChallengesQuery:
    rows = []

    @classmethod
    def filter_by(cls, **kwargs):
        matches = [r for r in cls.rows if all(getattr(r, k, None) == v for k, v in kwargs.items())]

        class _Result:
            @staticmethod
            def first():
                return matches[0] if matches else None

        return _Result()


class Challenges:
    query = _FakeChallengesQuery


class Awards:
    created = []

    def __init__(self, user_id=None, team_id=None, name=None, description=None, value=None, category=None):
        self.user_id = user_id
        self.team_id = team_id
        self.name = name
        self.description = description
        self.value = value
        self.category = category

    class _Query:
        @staticmethod
        def filter_by(**kwargs):
            matches = [
                a for a in Awards.created
                if all(getattr(a, k, None) == v for k, v in kwargs.items())
            ]

            class _Result:
                @staticmethod
                def first():
                    return matches[0] if matches else None

            return _Result()

    query = _Query


class _FakeSession:
    def add(self, obj):
        Awards.created.append(obj)

    def commit(self):
        pass


class _FakeDb:
    session = _FakeSession()


def _reset():
    Awards.created = []
    Challenges.query.rows = []


def _install_stubs():
    ctfd = types.ModuleType("CTFd")
    ctfd_models = types.ModuleType("CTFd.models")
    ctfd_models.db = _FakeDb
    ctfd_models.Awards = Awards
    ctfd_models.Challenges = Challenges

    ctfd_utils = types.ModuleType("CTFd.utils")
    ctfd_user = types.ModuleType("CTFd.utils.user")
    ctfd_user.get_current_user = lambda: SimpleNamespace(account_id=7, id=7, team_id=None)

    plugin_package = types.ModuleType("hint_wallet")
    plugin_package.__path__ = []

    orch_client_mod = types.ModuleType("hint_wallet.orchestrator_client")

    class _PlaceholderClient:
        @classmethod
        def from_env(cls):
            raise AssertionError("test must monkeypatch solve_hook.OrchestratorClient before calling a route")

    class _OrchestratorError(Exception):
        pass

    orch_client_mod.OrchestratorClient = _PlaceholderClient
    orch_client_mod.OrchestratorError = _OrchestratorError

    sys.modules.update({
        "CTFd": ctfd,
        "CTFd.models": ctfd_models,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.user": ctfd_user,
        "hint_wallet": plugin_package,
        "hint_wallet.orchestrator_client": orch_client_mod,
    })
    return orch_client_mod.OrchestratorError, ctfd_user


OrchestratorError, ctfd_user_mod = _install_stubs()

track_mapping_path = Path(__file__).resolve().parents[1] / "track_mapping.py"
tm_spec = importlib.util.spec_from_file_location("hint_wallet.track_mapping", track_mapping_path)
track_mapping = importlib.util.module_from_spec(tm_spec)
sys.modules["hint_wallet.track_mapping"] = track_mapping
tm_spec.loader.exec_module(track_mapping)

module_path = Path(__file__).resolve().parents[1] / "solve_hook.py"
spec = importlib.util.spec_from_file_location("hint_wallet.solve_hook", module_path)
solve_hook = importlib.util.module_from_spec(spec)
sys.modules["hint_wallet.solve_hook"] = solve_hook
spec.loader.exec_module(solve_hook)


@pytest.fixture
def app_client(monkeypatch):
    _reset()
    app = Flask(__name__)
    solve_hook.register(app)

    @app.route("/api/v1/challenges/attempt", methods=["POST"])
    def fake_attempt():
        # A real attempt route always returns success=True/status="correct"
        # for this test's purposes -- callers that want "incorrect" pass
        # a body flag the fake route honors, mirroring the real endpoint's
        # documented {"success": true, "data": {"status": ...}} contract.
        body = request.get_json(silent=True) or {}
        status = "correct" if body.get("_correct", True) else "incorrect"
        return jsonify(success=True, data={"status": status})

    monkeypatch.setattr(ctfd_user_mod, "get_current_user", lambda: SimpleNamespace(account_id=7, id=7, team_id=None))
    return app.test_client()


def _install_fake_orchestrator(monkeypatch, unlocked_result):
    class _FakeClient:
        def unlocked(self, owner_id, track, entry_name):
            if isinstance(unlocked_result, Exception):
                raise unlocked_result
            return unlocked_result

    class _Factory:
        @classmethod
        def from_env(cls):
            return _FakeClient()

    monkeypatch.setattr(solve_hook, "OrchestratorClient", _Factory)


def _seed_challenge(id=1, name="Bandit 0 -> 1", category="Linux Basics", value=100):
    Challenges.query.rows = [_FakeChallenge(id, name, category, value)]
    return id


# ── tests ────────────────────────────────────────────────────────────────

def test_no_hint_opened_applies_no_penalty(app_client, monkeypatch):
    challenge_id = _seed_challenge(value=100)
    _install_fake_orchestrator(monkeypatch, {"tier": None, "cost_percent": None})

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})

    assert Awards.created == []


def test_tier_opened_applies_percent_of_value_penalty(app_client, monkeypatch):
    challenge_id = _seed_challenge(value=100)
    _install_fake_orchestrator(monkeypatch, {"tier": 2, "cost_percent": 50})

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})

    assert len(Awards.created) == 1
    award = Awards.created[0]
    assert award.value == -50  # 50% of 100 points
    assert award.user_id == 7


def test_penalty_rounds_down_fractional_points(app_client, monkeypatch):
    challenge_id = _seed_challenge(value=99)
    _install_fake_orchestrator(monkeypatch, {"tier": 1, "cost_percent": 10})  # 9.9 -> floor 9

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})

    assert Awards.created[0].value == -9


def test_penalty_is_idempotent_against_a_duplicate_correct_observation(app_client, monkeypatch):
    challenge_id = _seed_challenge(value=100)
    _install_fake_orchestrator(monkeypatch, {"tier": 3, "cost_percent": 85})

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})
    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})

    assert len(Awards.created) == 1, "a repeated correct observation must not double-penalize"


def test_independent_penalties_across_different_challenges(app_client, monkeypatch):
    Challenges.query.rows = [
        _FakeChallenge(1, "Bandit 0 -> 1", "Linux Basics", 100),
        _FakeChallenge(2, "Bandit 1 -> 2", "Linux Basics", 200),
    ]

    def fake_unlocked_factory():
        class _FakeClient:
            def unlocked(self, owner_id, track, entry_name):
                if entry_name == "Bandit 0 -> 1":
                    return {"tier": 1, "cost_percent": 10}
                return {"tier": 3, "cost_percent": 85}

        class _Factory:
            @classmethod
            def from_env(cls):
                return _FakeClient()

        return _Factory

    monkeypatch.setattr(solve_hook, "OrchestratorClient", fake_unlocked_factory())

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": 1})
    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": 2})

    values = sorted(a.value for a in Awards.created)
    assert values == [-170, -10]  # -10% of 100, -85% of 200


def test_incorrect_submission_applies_no_penalty(app_client, monkeypatch):
    challenge_id = _seed_challenge(value=100)
    _install_fake_orchestrator(monkeypatch, {"tier": 3, "cost_percent": 85})

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "_correct": False})

    assert Awards.created == []


def test_unmapped_category_applies_no_penalty_and_never_calls_orchestrator(app_client, monkeypatch):
    challenge_id = _seed_challenge(category="Web Exploitation", value=100)  # not a hint-wallet track
    called = []

    class _FakeClient:
        def unlocked(self, *a, **kw):
            called.append(1)
            return {"tier": None, "cost_percent": None}

    class _Factory:
        @classmethod
        def from_env(cls):
            return _FakeClient()

    monkeypatch.setattr(solve_hook, "OrchestratorClient", _Factory)

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})

    assert Awards.created == []
    assert called == [], "a non-wallet-track challenge must never even query the orchestrator"


def test_orchestrator_unreachable_is_swallowed_and_applies_no_penalty(app_client, monkeypatch):
    challenge_id = _seed_challenge(value=100)
    _install_fake_orchestrator(monkeypatch, OrchestratorError("orchestrator unreachable: timeout"))

    resp = app_client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id})

    assert resp.status_code == 200, "a transient orchestrator failure must never break the flag-submission response"
    assert Awards.created == []


def test_unknown_challenge_id_applies_no_penalty(app_client, monkeypatch):
    Challenges.query.rows = []  # nothing seeded
    called = []

    class _FakeClient:
        def unlocked(self, *a, **kw):
            called.append(1)

    class _Factory:
        @classmethod
        def from_env(cls):
            return _FakeClient()

    monkeypatch.setattr(solve_hook, "OrchestratorClient", _Factory)

    app_client.post("/api/v1/challenges/attempt", json={"challenge_id": 999})

    assert Awards.created == []
    assert called == []


def test_non_attempt_paths_are_ignored(app_client):
    resp = app_client.get("/api/v1/challenges/attempt")  # wrong method, not POST
    assert resp.status_code in (404, 405)
    assert Awards.created == []
