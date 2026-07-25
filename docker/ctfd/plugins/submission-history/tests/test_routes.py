"""Tests routes.py's blueprint end-to-end via a real Flask test client.

Stubs CTFd internals routes.py imports at module level (none of which are
installed outside a real CTFd deployment) with plain Python doubles,
following the exact pattern hint-wallet/tests/test_routes.py and
instance-launcher/tests/test_stage_gating.py already use in this repo. Real
`flask` IS installed, so the blueprint is exercised through an actual Flask
app and test_client() -- route decorators, URL rules, and status codes are
all genuinely covered.

The fake `Solves`/`Challenges` query objects deliberately model the exact
chain routes.py actually calls (`.filter(...).join(...).add_columns(...)
.order_by(...).first()/.all()`), not a generic SQLAlchemy shim -- enough to
prove the real account-scoping logic (never someone else's solve, never an
unsolved challenge) without needing a real database.
"""
import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask


# ── Fake CTFd.models: Solves / Challenges ───────────────────────────────────

class _Column:
    """Stands in for a real SQLAlchemy InstrumentedAttribute -- supports
    `Column == value` (building a _Criterion routes.py's .filter() consumes)
    and a no-op `.desc()` (routes.py's order_by(Solves.date.desc()))."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return _Criterion(self.name, other)

    def __hash__(self):
        return hash(self.name)

    def desc(self):
        return self


class _Criterion:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def matches(self, obj):
        return getattr(obj, self.name, None) == self.value


class _FakeSolve:
    def __init__(self, challenge_id, provided, date, team_id=None, user_id=None):
        self.challenge_id = challenge_id
        self.provided = provided
        self.date = date
        self.team_id = team_id
        self.user_id = user_id


_CHALLENGE_NAMES = {}  # challenge_id -> name, set up per test
_SOLVES_TABLE = []  # list[_FakeSolve], set up per test


class _FakeQuery:
    """Mutates and returns self at each chained call -- routes.py never
    reuses an intermediate query object after chaining further, so this
    doesn't need real SQLAlchemy immutability semantics."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._add_name = False

    def filter(self, *criteria):
        self._rows = [r for r in self._rows if all(c.matches(r) for c in criteria)]
        return self

    def join(self, *args, **kwargs):
        return self  # join target is irrelevant to this fake -- name lookup happens in _pack()

    def add_columns(self, *_cols):
        self._add_name = True
        return self

    def order_by(self, *_args, **_kwargs):
        self._rows = sorted(self._rows, key=lambda s: s.date or datetime.min, reverse=True)
        return self

    def _pack(self, row):
        if self._add_name:
            return (row, _CHALLENGE_NAMES.get(row.challenge_id, "Unknown Challenge"))
        return row

    def first(self):
        return self._pack(self._rows[0]) if self._rows else None

    def all(self):
        return [self._pack(r) for r in self._rows]


class _SolvesQueryFactory:
    @staticmethod
    def filter(*criteria):
        return _FakeQuery(_SOLVES_TABLE).filter(*criteria)


class Solves:
    query = _SolvesQueryFactory
    team_id = _Column("team_id")
    user_id = _Column("user_id")
    challenge_id = _Column("challenge_id")
    date = _Column("date")


class Challenges:
    id = _Column("id")
    name = _Column("name")


def _install_stubs():
    ctfd = types.ModuleType("CTFd")
    ctfd_models = types.ModuleType("CTFd.models")
    ctfd_models.Solves = Solves
    ctfd_models.Challenges = Challenges

    ctfd_utils = types.ModuleType("CTFd.utils")
    ctfd_utils.get_config = lambda key: "users"  # default: user mode, overridden per-test via monkeypatch

    ctfd_decorators = types.ModuleType("CTFd.utils.decorators")
    ctfd_decorators.authed_only = lambda f: f

    ctfd_user = types.ModuleType("CTFd.utils.user")
    ctfd_user.get_current_user = lambda: SimpleNamespace(account_id=1)

    sys.modules.update({
        "CTFd": ctfd,
        "CTFd.models": ctfd_models,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.decorators": ctfd_decorators,
        "CTFd.utils.user": ctfd_user,
    })


_install_stubs()

module_path = Path(__file__).resolve().parents[1] / "routes.py"
spec = importlib.util.spec_from_file_location("submission_history.routes", module_path)
routes = importlib.util.module_from_spec(spec)
sys.modules["submission_history.routes"] = routes
spec.loader.exec_module(routes)


@pytest.fixture
def app_client():
    app = Flask(__name__)
    app.register_blueprint(routes.submission_history_bp)
    global _SOLVES_TABLE, _CHALLENGE_NAMES
    _SOLVES_TABLE = []
    _CHALLENGE_NAMES = {}
    return app.test_client()


def _as_user(monkeypatch, account_id, user_mode="users"):
    monkeypatch.setattr(routes, "get_current_user", lambda: SimpleNamespace(account_id=account_id))
    monkeypatch.setattr(routes, "get_config", lambda key: user_mode)


def _seed(challenge_id, name, provided, date=None, team_id=None, user_id=None):
    _CHALLENGE_NAMES[challenge_id] = name
    _SOLVES_TABLE.append(
        _FakeSolve(challenge_id, provided, date or datetime(2026, 1, 1), team_id=team_id, user_id=user_id)
    )


# ── /api/solve/<id> ──────────────────────────────────────────────────────

def test_api_solve_returns_own_provided_flag(app_client, monkeypatch):
    _as_user(monkeypatch, account_id=42, user_mode="users")
    _seed(1, "Bandit 0 -> 1", "the-flag-i-typed", user_id=42)

    resp = app_client.get("/plugins/submission-history/api/solve/1")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["challenge_id"] == 1
    assert body["challenge_name"] == "Bandit 0 -> 1"
    assert body["provided"] == "the-flag-i-typed"


def test_api_solve_404_when_this_account_never_solved_it(app_client, monkeypatch):
    _as_user(monkeypatch, account_id=42, user_mode="users")
    # No seeded solve at all for challenge 1.

    resp = app_client.get("/plugins/submission-history/api/solve/1")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_solved"


def test_api_solve_never_leaks_another_accounts_solve(app_client, monkeypatch):
    """The core scoping guarantee: a challenge solved by a DIFFERENT
    account must come back as 404 (indistinguishable from "no one solved
    it"), never as someone else's submitted flag."""
    _as_user(monkeypatch, account_id=42, user_mode="users")
    _seed(1, "Bandit 0 -> 1", "someone-elses-flag", user_id=999)

    resp = app_client.get("/plugins/submission-history/api/solve/1")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_solved"


def test_api_solve_uses_team_id_in_team_mode(app_client, monkeypatch):
    """CTFd's Solves table has separate team_id/user_id columns -- in team
    mode this account's solve is stored under team_id, not user_id, and
    the query must follow user_mode (matches wargame-stages/routes.py's
    _scoreboard() -- see routes.py's module docstring)."""
    _as_user(monkeypatch, account_id=7, user_mode="teams")
    _seed(1, "Krypton 1", "team-flag", team_id=7, user_id=None)

    resp = app_client.get("/plugins/submission-history/api/solve/1")

    assert resp.status_code == 200
    assert resp.get_json()["provided"] == "team-flag"


def test_api_solve_team_mode_does_not_match_on_user_id(app_client, monkeypatch):
    """In team mode, a solve keyed only by user_id (never this account's
    team_id) must not match -- proves the account_column switch is actually
    load-bearing, not just decorative."""
    _as_user(monkeypatch, account_id=7, user_mode="teams")
    _seed(1, "Krypton 1", "someones-user-mode-flag", user_id=7, team_id=None)

    resp = app_client.get("/plugins/submission-history/api/solve/1")

    assert resp.status_code == 404


# ── /api/solves ──────────────────────────────────────────────────────────

def test_api_solves_lists_only_this_accounts_solves_newest_first(app_client, monkeypatch):
    _as_user(monkeypatch, account_id=42, user_mode="users")
    _seed(1, "Bandit 0 -> 1", "flag-1", date=datetime(2026, 1, 1), user_id=42)
    _seed(2, "Bandit 1 -> 2", "flag-2", date=datetime(2026, 1, 3), user_id=42)
    _seed(3, "Krypton 1", "not-mine", date=datetime(2026, 1, 2), user_id=999)  # different account

    resp = app_client.get("/plugins/submission-history/api/solves")

    assert resp.status_code == 200
    solves = resp.get_json()["solves"]
    assert [s["challenge_id"] for s in solves] == [2, 1], "expected newest-first, own solves only"
    assert all(s["provided"] != "not-mine" for s in solves)


def test_api_solves_empty_when_account_has_no_solves(app_client, monkeypatch):
    _as_user(monkeypatch, account_id=42, user_mode="users")

    resp = app_client.get("/plugins/submission-history/api/solves")

    assert resp.status_code == 200
    assert resp.get_json()["solves"] == []


# ── /solves (HTML page) ──────────────────────────────────────────────────

def test_solves_page_renders_for_authenticated_account(app_client, monkeypatch):
    _as_user(monkeypatch, account_id=42, user_mode="users")
    _seed(1, "Bandit 0 -> 1", "flag-1", user_id=42)

    resp = app_client.get("/plugins/submission-history/solves")

    assert resp.status_code == 200
    assert b"flag-1" in resp.data
    assert b"Bandit 0 -&gt; 1" in resp.data or b"Bandit 0 -> 1" in resp.data
