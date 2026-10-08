"""Regression tests for the stage-gating fix in routes.py.

wargame-stages (docker/ctfd/plugins/wargame-stages/routes.py) is the ONLY
thing gating staged content -- it flips a CTFd challenge's `state` between
"hidden" (mapped into a not-yet-started stage) and "visible" (stage
started). Before this fix, instance-launcher's launch/status endpoints
never looked at that state at all: since CTFd challenge ids are small
sequential integers, any authenticated player could enumerate ids and POST
to a launch endpoint for a challenge in a stage that hadn't opened yet and
get a live container with real connect info ahead of schedule.

These tests exercise the actual route functions (not just a helper in
isolation) with a real Flask request context, so a regression that only
guards one of the three entrypoints (launch / api_status / api_launch)
would be caught. Stubs CTFd/SQLAlchemy-dialect internals routes.py imports
at module level (none of which are installed outside a real CTFd
deployment), following the same pattern test_secret_scrubbing.py already
uses in this directory. Real `flask` IS installed, so requests are driven
through actual Flask test request contexts and abort()'s real
HTTPException.
"""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from werkzeug.exceptions import HTTPException


# ── Marker-setting auth decorator stubs ─────────────────────────────────────
# See the identical block in test_secret_scrubbing.py for why these are not
# bare `lambda f: f`: the whole suite runs with the auth decorators
# neutralized, so without an observable marker nothing here (or in the other
# file) could catch a deleted @authed_only/@admins_only.


def _marked_authed_only(func):
    setattr(func, "__authed__", True)
    return func


def _marked_admins_only(func):
    setattr(func, "__admins_only__", True)
    return func


# ── Fake CTFd.models ─────────────────────────────────────────────────────────

class _FakeChallenge:
    def __init__(self, id, state="visible"):
        self.id = id
        self.state = state


class _ChallengesQuery:
    rows: dict = {}

    @classmethod
    def get_or_404(cls, challenge_id):
        challenge = cls.rows.get(challenge_id)
        if challenge is None:
            from flask import abort
            abort(404)
        return challenge


class Challenges:
    query = _ChallengesQuery


class _FakeDb:
    class Model:
        pass

    class Column:
        def __init__(self, *a, **kw):
            pass

    class session:
        @staticmethod
        def rollback():
            pass

        @staticmethod
        def commit():
            pass

        @staticmethod
        def add(_obj):
            pass


class _FakeColumn:
    """Stands in for a real SQLAlchemy InstrumentedAttribute -- routes.py's
    _group_dynamic_flags builds a `.in_(...)` filter expression with it."""

    def in_(self, values):
        return values


class _FlagsQuery:
    @classmethod
    def filter(cls, *args, **kwargs):
        return cls

    @classmethod
    def all(cls):
        return []


class Flags:
    query = _FlagsQuery
    challenge_id = _FakeColumn()
    type = _FakeColumn()


# ── Fake instance_launcher.models.InstanceChallengeConfig ──────────────────

class _FakeConfig:
    def __init__(self, challenge_id, instance_type="web-app", instance_group=None):
        self.challenge_id = challenge_id
        self.instance_type = instance_type
        self.instance_group = instance_group
        self.image = "example/image:latest"
        self.port = 8080
        self.show_launcher = True

    def resolved_instance_key(self):
        return f"challenge-{self.challenge_id}"

    def to_orchestrator_spec(self):
        return {"image": self.image, "port": self.port}


class _ConfigQuery:
    rows: dict = {}

    @classmethod
    def filter_by(cls, **criteria):
        challenge_id = criteria.get("challenge_id")
        instance_group = criteria.get("instance_group")

        class _Result:
            @staticmethod
            def first():
                return cls.rows.get(challenge_id) if challenge_id is not None else None

            @staticmethod
            def all():
                if instance_group is not None:
                    return [c for c in cls.rows.values() if c.instance_group == instance_group]
                return list(cls.rows.values())
        return _Result()


class _FakeInstanceChallengeConfig:
    query = _ConfigQuery


# ── Fake orchestrator client ────────────────────────────────────────────────

class _FakeOrchestratorClient:
    def __init__(self):
        self.calls = []

    @classmethod
    def from_env(cls):
        return cls()

    def create_or_get(self, instance_type, owner_id, instance_key, spec, relaunch=False):
        self.calls.append(("create_or_get", instance_type, owner_id, instance_key, relaunch))
        return {"access": {"url": f"https://{instance_key}.apps.ctf.local"}}

    def reboot(self, owner_id, instance_key):
        self.calls.append(("reboot", owner_id, instance_key))

    def extend_shutdown(self, owner_id, instance_key):
        self.calls.append(("extend", owner_id, instance_key))

    def get(self, owner_id, instance_key):
        return {"access": {"url": f"https://{instance_key}.apps.ctf.local"}}


def _install_stubs():
    ctfd = types.ModuleType("CTFd")
    ctfd_models = types.ModuleType("CTFd.models")
    ctfd_models.db = _FakeDb
    ctfd_models.Challenges = Challenges
    ctfd_models.Flags = Flags

    ctfd_plugins = types.ModuleType("CTFd.plugins")
    ctfd_plugins.bypass_csrf_protection = lambda f: f

    ctfd_utils = types.ModuleType("CTFd.utils")
    ctfd_decorators = types.ModuleType("CTFd.utils.decorators")
    ctfd_decorators.admins_only = _marked_admins_only
    ctfd_decorators.authed_only = _marked_authed_only
    ctfd_user = types.ModuleType("CTFd.utils.user")
    ctfd_user.get_current_user = lambda: SimpleNamespace(account_id=1)

    sa = types.ModuleType("sqlalchemy")
    sa_dialects = types.ModuleType("sqlalchemy.dialects")
    sa_mysql = types.ModuleType("sqlalchemy.dialects.mysql")
    sa_mysql.insert = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("mysql insert path unused"))
    sa_sqlite = types.ModuleType("sqlalchemy.dialects.sqlite")
    sa_sqlite.insert = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("sqlite insert path unused"))

    plugin_package = types.ModuleType("instance_launcher")
    plugin_package.__path__ = []

    models_mod = types.ModuleType("instance_launcher.models")
    models_mod.InstanceChallengeConfig = _FakeInstanceChallengeConfig
    models_mod.TeamChallengeSecret = SimpleNamespace

    orch_client_mod = types.ModuleType("instance_launcher.orchestrator_client")
    orch_client_mod.OrchestratorClient = _FakeOrchestratorClient
    orch_client_mod.OrchestratorError = Exception
    orch_client_mod.read_secret = lambda name: ""

    actions_mod = types.ModuleType("instance_launcher.actions")
    actions_mod.is_valid_action = lambda a: a in (None, "reboot", "relaunch", "extend")

    sys.modules.update({
        "CTFd": ctfd,
        "CTFd.models": ctfd_models,
        "CTFd.plugins": ctfd_plugins,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.decorators": ctfd_decorators,
        "CTFd.utils.user": ctfd_user,
        "sqlalchemy": sa,
        "sqlalchemy.dialects": sa_dialects,
        "sqlalchemy.dialects.mysql": sa_mysql,
        "sqlalchemy.dialects.sqlite": sa_sqlite,
        "instance_launcher": plugin_package,
        "instance_launcher.models": models_mod,
        "instance_launcher.orchestrator_client": orch_client_mod,
        "instance_launcher.actions": actions_mod,
    })


_install_stubs()

module_path = Path(__file__).resolve().parents[1] / "routes.py"
spec = importlib.util.spec_from_file_location("instance_launcher.routes", module_path)
routes = importlib.util.module_from_spec(spec)
sys.modules["instance_launcher.routes"] = routes
spec.loader.exec_module(routes)

app = Flask(__name__)


def setup_function(_):
    _ChallengesQuery.rows = {}
    _ConfigQuery.rows = {}


# ── /launch/<id> (HTML route) ───────────────────────────────────────────────

def test_html_launch_rejects_hidden_challenge():
    """A challenge mapped into a stage that hasn't started yet (state
    "hidden", set by wargame-stages.sync()) must never reach _run_action --
    the abort happens before config resolution/render_template, so a hidden
    challenge with no config still correctly rejects rather than 404ing."""
    _ChallengesQuery.rows[5] = _FakeChallenge(5, state="hidden")
    _ConfigQuery.rows[5] = _FakeConfig(5)

    with app.test_request_context("/plugins/instance-launcher/launch/5", method="GET"):
        with pytest.raises(HTTPException) as excinfo:
            routes.launch(5)
    assert excinfo.value.code == 403


def test_html_launch_allows_visible_challenge_config_lookup():
    """A visible challenge must get past the gate (the eventual outbound
    call to the orchestrator is exercised via the JSON API tests below,
    which don't require stubbing template rendering)."""
    _ChallengesQuery.rows[6] = _FakeChallenge(6, state="visible")
    _ConfigQuery.rows[6] = _FakeConfig(6)

    with app.test_request_context("/plugins/instance-launcher/launch/6", method="GET"):
        # Should not raise/abort past the visibility gate. If it reaches
        # render_template() (no CTFd Jinja env available in this stub
        # environment), that itself proves the gate let it through.
        try:
            routes.launch(6)
        except HTTPException as exc:
            pytest.fail(f"visible challenge was rejected by the gate: {exc}")
        except Exception:
            # Any other failure is expected this far into a stubbed
            # environment (no real Jinja template loader) -- what matters
            # is that it is NOT the 403 from _reject_if_not_visible.
            pass


# ── /api/status/<id> ─────────────────────────────────────────────────────────

def test_api_status_rejects_hidden_challenge():
    _ChallengesQuery.rows[7] = _FakeChallenge(7, state="hidden")
    _ConfigQuery.rows[7] = _FakeConfig(7)

    with app.test_request_context("/plugins/instance-launcher/api/status/7", method="GET"):
        with pytest.raises(HTTPException) as excinfo:
            routes.api_status(7)
    assert excinfo.value.code == 403


def test_api_status_allows_visible_challenge():
    _ChallengesQuery.rows[8] = _FakeChallenge(8, state="visible")
    _ConfigQuery.rows[8] = _FakeConfig(8)

    with app.test_request_context("/plugins/instance-launcher/api/status/8", method="GET"):
        body, code = routes.api_status(8)
    assert code == 200
    assert body["has_environment"] is True


def test_api_status_visible_but_unconfigured_challenge_is_not_blocked():
    """No InstanceChallengeConfig row at all (this challenge has no
    launchable environment) is a distinct, pre-existing case from being
    hidden -- must stay a normal 200/has_environment:False, not conflated
    with the new gate."""
    _ChallengesQuery.rows[9] = _FakeChallenge(9, state="visible")

    with app.test_request_context("/plugins/instance-launcher/api/status/9", method="GET"):
        body, code = routes.api_status(9)
    assert code == 200
    assert body == {"has_environment": False}


# ── /api/launch/<id> ─────────────────────────────────────────────────────────

def test_api_launch_rejects_hidden_challenge():
    """The actual exploit this fix closes: an authenticated player POSTing
    directly to /api/launch/<id> for a challenge whose stage hasn't opened
    (state "hidden") must be rejected before any orchestrator call is made."""
    _ChallengesQuery.rows[10] = _FakeChallenge(10, state="hidden")
    config = _FakeConfig(10)
    _ConfigQuery.rows[10] = config

    with app.test_request_context(
        "/plugins/instance-launcher/api/launch/10",
        method="POST",
        json={"action": None},
    ):
        with pytest.raises(HTTPException) as excinfo:
            routes.api_launch(10)
    assert excinfo.value.code == 403


def test_api_launch_succeeds_for_visible_challenge():
    """The control case: once wargame-stages flips a challenge's state to
    "visible" (stage started), launching it must still work exactly as
    before this fix."""
    _ChallengesQuery.rows[11] = _FakeChallenge(11, state="visible")
    config = _FakeConfig(11)
    _ConfigQuery.rows[11] = config

    with app.test_request_context(
        "/plugins/instance-launcher/api/launch/11",
        method="POST",
        json={"action": None},
    ):
        body, code = routes.api_launch(11)
    assert code == 200
    assert body["success"] is True
    assert body["status"]["access"]["url"] == "https://challenge-11.apps.ctf.local"


# ── /admin/mappings/sync shared-secret auth ────────────────────────────────
# X-Sync-Auth is compared with hmac.compare_digest, which raises TypeError on
# `str` operands containing any byte >= 0x80 -- so before this was encoded on
# both sides, a request carrying a single non-ASCII byte in the header
# produced an unhandled 500 on a machine-to-machine endpoint whose only
# correct failure mode is 401. Flask/Werkzeug hands header values over as
# latin-1-decoded str, so a raw 0x80-0xFF byte on the wire arrives here
# exactly as a non-ASCII str.

SECRET = "shared-secret-value"


@pytest.fixture
def sync_client(monkeypatch):
    monkeypatch.setattr(routes, "read_secret", lambda _name: SECRET)
    sync_app = Flask(__name__, static_folder=None)
    sync_app.register_blueprint(routes.instance_launcher_bp)
    return sync_app.test_client()


def _sync_body():
    return {"challenge_name": "Bandit 0 -> 1", "instance_type": "web-app"}


def test_sync_without_the_header_is_401(sync_client):
    assert sync_client.post("/plugins/instance-launcher/admin/mappings/sync", json=_sync_body()).status_code == 401


def test_sync_with_a_wrong_secret_is_401(sync_client):
    resp = sync_client.post(
        "/plugins/instance-launcher/admin/mappings/sync",
        json=_sync_body(),
        headers={"X-Sync-Auth": "not-the-secret"},
    )
    assert resp.status_code == 401


def test_sync_with_the_right_secret_is_200(sync_client, monkeypatch):
    """The control case: the endpoint still works for scripts/challenges-load.sh."""
    class _NamedQuery:
        @staticmethod
        def filter_by(**_criteria):
            return SimpleNamespace(first=lambda: _FakeChallenge(5, state="visible"))

    monkeypatch.setattr(routes.Challenges, "query", _NamedQuery)
    monkeypatch.setattr(routes.InstanceChallengeConfig, "query", _ConfigQuery)
    _ConfigQuery.rows[5] = _FakeConfig(5)

    resp = sync_client.post(
        "/plugins/instance-launcher/admin/mappings/sync",
        json=_sync_body(),
        headers={"X-Sync-Auth": SECRET},
    )

    assert resp.status_code == 200
    assert resp.get_json()["challenge_id"] == 5


def test_sync_with_an_empty_configured_secret_is_401(sync_client, monkeypatch):
    """Fail closed when the deployment never provisioned plugin_shared_secret,
    rather than letting any caller through."""
    monkeypatch.setattr(routes, "read_secret", lambda _name: "")
    resp = sync_client.post(
        "/plugins/instance-launcher/admin/mappings/sync",
        json=_sync_body(),
        headers={"X-Sync-Auth": ""},
    )
    assert resp.status_code == 401


def test_sync_with_a_non_ascii_header_is_401_not_500(sync_client):
    resp = sync_client.post(
        "/plugins/instance-launcher/admin/mappings/sync",
        json=_sync_body(),
        headers={"X-Sync-Auth": "café-" + SECRET},
    )
    assert resp.status_code == 401, f"expected a clean 401, got {resp.status_code}"


# ── route access-control contract ─────────────────────────────────────────
# Every behavioral test in this plugin's suite runs with the auth decorators
# stubbed to mark-and-call-through, so none of them can distinguish
# "correctly authed" from "decorator deleted". This table-driven test closes
# that gap by walking the blueprint's real url_map and asserting each rule
# carries the marker its decorator implies -- so dropping an admin guard (or
# adding an unguarded route) now fails CI instead of resting on code review
# alone.

EXPECTED_AUTH = {
    "instance_launcher.launch": {"authed": True, "admins": False},
    "instance_launcher.api_status": {"authed": True, "admins": False},
    "instance_launcher.api_launch": {"authed": True, "admins": False},
    "instance_launcher.admin_mappings": {"authed": False, "admins": True},
    "instance_launcher.admin_delete_mapping": {"authed": False, "admins": True},
    # Machine-to-machine: authenticated by X-Sync-Auth, not a CTFd session.
    "instance_launcher.sync_mapping": {"authed": False, "admins": False},
}


@pytest.mark.parametrize("endpoint,expected", sorted(EXPECTED_AUTH.items()))
def test_every_route_carries_the_expected_auth_decorator(endpoint, expected):
    contract_app = Flask(__name__, static_folder=None)
    contract_app.register_blueprint(routes.instance_launcher_bp)
    view = contract_app.view_functions[endpoint]
    assert getattr(view, "__authed__", False) is expected["authed"]
    assert getattr(view, "__admins_only__", False) is expected["admins"]


def test_url_map_has_no_routes_beyond_the_ones_this_test_asserts():
    contract_app = Flask(__name__, static_folder=None)
    contract_app.register_blueprint(routes.instance_launcher_bp)
    assert {rule.endpoint for rule in contract_app.url_map.iter_rules()} == set(EXPECTED_AUTH)
