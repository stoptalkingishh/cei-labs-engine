"""Regression tests for routes.py's _persist_and_scrub_secrets — the one
place that stands between the orchestrator's `access` dict (displayed
VERBATIM to the player as connect info) and a per-team flag value riding
that same dict. If this stops popping a level's secret out of `access`
before a status dict is rendered/returned, a player gets their own answer
handed to them unearned, and — since TeamChallengeSecret is keyed by
(owner_id, challenge_id) — cross-team isolation is also exercised here:
two teams' secrets for the *same* challenge must never collide or leak
into each other's persisted row or scrubbed response.

Stubs CTFd/Flask/SQLAlchemy-dialect internals routes.py imports at module
level (none of which are installed outside a real CTFd deployment) with
plain Python doubles, following the same pattern test_flags.py already
uses in this directory. Real `flask` and `requests` ARE installed (both are
also orchestrator/orchestrator_client.py dependencies), so those aren't
stubbed.
"""
import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


# ── Fake CTFd.models ─────────────────────────────────────────────────────────

class _FakeSecretRow:
    def __init__(self, owner_id, challenge_id, value):
        self.owner_id = owner_id
        self.challenge_id = challenge_id
        self.value = value
        self.updated_at = None


class _FakeSecretTable:
    """Backing store for TeamChallengeSecret, keyed by (owner_id, challenge_id)
    exactly like the real table's UniqueConstraint -- lets a test assert two
    owners never collide on the same challenge_id."""
    rows: dict = {}

    @classmethod
    def reset(cls):
        cls.rows = {}


class _FakeSecretQuery:
    @staticmethod
    def filter_by(**criteria):
        owner_id = criteria.get("owner_id")
        challenge_id = criteria.get("challenge_id")

        class _Result:
            @staticmethod
            def first():
                return _FakeSecretTable.rows.get((owner_id, challenge_id))
        return _Result()


class TeamChallengeSecret:
    __tablename__ = "instance_launcher_team_secrets"
    __table__ = SimpleNamespace(name="instance_launcher_team_secrets")
    query = _FakeSecretQuery

    def __init__(self, owner_id, challenge_id, value):
        self.owner_id = owner_id
        self.challenge_id = challenge_id
        self.value = value
        self.updated_at = None
        _FakeSecretTable.rows[(owner_id, challenge_id)] = self


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)
        _FakeSecretTable.rows[(obj.owner_id, obj.challenge_id)] = obj

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def execute(self, *a, **kw):
        raise AssertionError("real SQL insert path should not run in this test (dialect is 'faketest')")


class _FakeDb:
    session = _FakeSession()
    engine = SimpleNamespace(dialect=SimpleNamespace(name="faketest"))

    class Model:
        pass

    class Column:
        def __init__(self, *a, **kw):
            pass


class _FakeFlag:
    def __init__(self, challenge_id, data, type_):
        self.challenge_id = challenge_id
        self.data = data
        self.type = type_


class _FlagsQuery:
    rows: list = []

    @classmethod
    def filter(cls, *args, **kwargs):
        return cls

    @classmethod
    def all(cls):
        return cls.rows


class _FakeColumn:
    """Stands in for a real SQLAlchemy InstrumentedAttribute -- routes.py's
    _group_dynamic_flags builds a `.in_(...)` filter expression with it. The
    fake query below ignores the resulting expression and returns whatever
    rows a test set up directly, so this only needs to not crash."""

    def in_(self, values):
        return values


class Flags:
    query = _FlagsQuery
    challenge_id = _FakeColumn()
    type = _FakeColumn()


class Challenges:
    query = None

    @staticmethod
    def get_or_404(_id):
        raise NotImplementedError


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
    ctfd_decorators.admins_only = lambda f: f
    ctfd_decorators.authed_only = lambda f: f
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
    class _ConfigQuery:
        @staticmethod
        def filter_by(**criteria):
            class _Result:
                @staticmethod
                def all():
                    # Not exercised for correctness here: the fake Flags
                    # query below ignores its filter arguments and returns
                    # whatever test rows were set up directly, so which
                    # sibling challenge_ids this resolves to doesn't change
                    # a test's outcome -- only that this call doesn't crash.
                    return []
            return _Result()

    class _FakeInstanceChallengeConfig:
        query = _ConfigQuery

    models_mod = types.ModuleType("instance_launcher.models")
    models_mod.InstanceChallengeConfig = _FakeInstanceChallengeConfig
    models_mod.TeamChallengeSecret = TeamChallengeSecret

    orch_client_mod = types.ModuleType("instance_launcher.orchestrator_client")
    orch_client_mod.OrchestratorClient = SimpleNamespace
    orch_client_mod.OrchestratorError = Exception
    orch_client_mod.read_secret = lambda name: ""

    actions_mod = types.ModuleType("instance_launcher.actions")
    actions_mod.is_valid_action = lambda a: True

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


def _config(instance_group=None, challenge_id=42):
    return SimpleNamespace(instance_group=instance_group, challenge_id=challenge_id)


def setup_function(_):
    _FakeSecretTable.reset()
    _FlagsQuery.rows = []
    _FakeDb.session = _FakeSession()
    routes.db = _FakeDb  # in case a prior test rebound it


def test_flag_value_is_removed_from_access_before_it_would_reach_the_player():
    _FlagsQuery.rows = [_FakeFlag(challenge_id=42, data="krypton2", type_="per_team_dynamic")]
    status = {
        "access": {
            "url": "https://team-1-krypton.apps.ctf.local",
            "vnc_password": "not-a-secret-connect-info",
            "krypton2": "THE-REAL-FLAG-VALUE",
        }
    }

    routes._persist_and_scrub_secrets(status, _config(), owner_id="team-1")

    assert "krypton2" not in status["access"], "flag value leaked into the player-facing access dict"
    # Ordinary connect info the player IS supposed to see must survive.
    assert status["access"]["url"] == "https://team-1-krypton.apps.ctf.local"
    assert status["access"]["vnc_password"] == "not-a-secret-connect-info"


def test_scrubbed_value_is_persisted_for_later_flag_validation():
    _FlagsQuery.rows = [_FakeFlag(challenge_id=42, data="krypton2", type_="per_team_dynamic")]
    status = {"access": {"krypton2": "THE-REAL-FLAG-VALUE"}}

    routes._persist_and_scrub_secrets(status, _config(), owner_id="team-1")

    assert _FakeSecretTable.rows[("team-1", 42)].value == "THE-REAL-FLAG-VALUE"
    assert _FakeDb.session.committed is True


def test_two_teams_sharing_a_challenge_never_collide_or_cross_leak():
    """Same challenge_id, two different owner_ids -- each team's launch
    response must only ever be scrubbed of / see ITS OWN secret. This is
    the property that makes GET/POST responses IDOR-safe: TeamChallengeSecret
    is keyed by (owner_id, challenge_id), and owner_id here is exactly what
    routes.py always derives server-side from get_current_user(), never
    from a URL/body parameter a player could tamper with."""
    _FlagsQuery.rows = [_FakeFlag(challenge_id=42, data="krypton2", type_="per_team_dynamic")]

    status_a = {"access": {"krypton2": "TEAM-A-FLAG"}}
    status_b = {"access": {"krypton2": "TEAM-B-FLAG"}}

    routes._persist_and_scrub_secrets(status_a, _config(), owner_id="team-a")
    routes._persist_and_scrub_secrets(status_b, _config(), owner_id="team-b")

    assert "krypton2" not in status_a["access"]
    assert "krypton2" not in status_b["access"]
    assert _FakeSecretTable.rows[("team-a", 42)].value == "TEAM-A-FLAG"
    assert _FakeSecretTable.rows[("team-b", 42)].value == "TEAM-B-FLAG"
    # Confirms the two rows are genuinely distinct storage slots, not one
    # shared-by-challenge_id row that the second write clobbered.
    assert _FakeSecretTable.rows[("team-a", 42)].value != _FakeSecretTable.rows[("team-b", 42)].value


def test_access_with_no_matching_flag_keys_is_left_untouched():
    _FlagsQuery.rows = [_FakeFlag(challenge_id=42, data="krypton2", type_="per_team_dynamic")]
    status = {"access": {"url": "https://example", "ssh_password": "abc123"}}

    routes._persist_and_scrub_secrets(status, _config(), owner_id="team-1")

    assert status["access"] == {"url": "https://example", "ssh_password": "abc123"}
    assert _FakeSecretTable.rows == {}


def test_missing_access_or_status_is_a_noop():
    routes._persist_and_scrub_secrets(None, _config(), owner_id="team-1")
    routes._persist_and_scrub_secrets({}, _config(), owner_id="team-1")
    routes._persist_and_scrub_secrets({"access": {}}, _config(), owner_id="team-1")
    # No exception is the assertion; nothing should ever be persisted.
    assert _FakeSecretTable.rows == {}


def test_instance_group_siblings_all_get_scrubbed_from_one_shared_status():
    """A boot2root box: several challenge_ids share one instance_group and
    therefore one launch response, each contributing its own level key."""
    _FlagsQuery.rows = [
        _FakeFlag(challenge_id=10, data="level1", type_="per_team_dynamic"),
        _FakeFlag(challenge_id=11, data="level2", type_="per_team_dynamic_alpha"),
        _FakeFlag(challenge_id=12, data="level3", type_="per_team_dynamic_fixed"),
    ]
    status = {
        "access": {
            "url": "https://team-1-box.apps.ctf.local",
            "level1": "flag-one",
            "level2": "flagtwo",
            "level3": "FIXEDLENGTHFLAG12345678901234567",
        }
    }

    routes._persist_and_scrub_secrets(status, _config(instance_group="boot2root"), owner_id="team-1")

    assert status["access"] == {"url": "https://team-1-box.apps.ctf.local"}
    assert _FakeSecretTable.rows[("team-1", 10)].value == "flag-one"
    assert _FakeSecretTable.rows[("team-1", 11)].value == "flagtwo"
    assert _FakeSecretTable.rows[("team-1", 12)].value == "FIXEDLENGTHFLAG12345678901234567"
