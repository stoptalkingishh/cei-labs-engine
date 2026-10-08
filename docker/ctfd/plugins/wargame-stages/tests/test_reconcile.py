"""Regression tests for automatic hide-by-default reconciliation.

Before this fix, a challenge only became CTFd-`hidden` when an admin
manually clicked "Sync" on the wargame-stages admin page, and only if the
stage was still `pending`. Nothing forced that to happen on deploy or on
app start, so newly-deployed/re-synced challenges sat visible under CTFd's
Challenges tab by default -- the actual bug this PR fixes (the scoreboard
visibility toggle was unaffected and worked correctly all along, which is
why it looked like only "half" the feature was broken).

These tests exercise `_reconcile_stage` / `reconcile_all_pending` in
`routes.py` against a small in-memory fake ORM built to support exactly the
query patterns those functions use (filter_by/filter/in_/~/all/delete) --
not a general SQLAlchemy stand-in, following the same "stub CTFd/SQLAlchemy
internals at module level" pattern instance-launcher/tests/test_stage_gating.py
already uses in this codebase.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest


# ── Minimal in-memory fake ORM ──────────────────────────────────────────────

class _Predicate:
    """Callable row-predicate that also supports `~`, like a real SQLAlchemy
    BinaryExpression does -- routes.py negates an `.in_()` result with `~`."""

    def __init__(self, fn):
        self._fn = fn

    def __call__(self, row):
        return self._fn(row)

    def __invert__(self):
        return _Predicate(lambda row: not self._fn(row))


class _Col:
    """Stands in for a SQLAlchemy InstrumentedAttribute on a fake model."""

    def __init__(self, name):
        self.name = name

    def in_(self, values):
        values = set(values)
        return _Predicate(lambda row: getattr(row, self.name) in values)

    def __ne__(self, other):
        return _Predicate(lambda row: getattr(row, self.name) != other)


class _FakeQuery:
    """A tiny in-memory stand-in for a SQLAlchemy Query over one table's rows."""

    def __init__(self, rows):
        self._rows = rows  # list of live row objects (mutated in place, like a real session)

    def filter_by(self, **criteria):
        matched = [r for r in self._rows if all(getattr(r, k) == v for k, v in criteria.items())]
        return _FakeQuery(matched)

    def filter(self, *predicates):
        rows = self._rows
        for predicate in predicates:
            if predicate is True:
                continue
            rows = [r for r in rows if predicate(r)]
        return _FakeQuery(rows)

    def order_by(self, *_a, **_kw):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def first_or_404(self):
        if not self._rows:
            raise LookupError("404")
        return self._rows[0]

    def count(self):
        return len(self._rows)

    def delete(self, synchronize_session=False):
        for row in list(self._rows):
            ALL_TABLES[type(row)].remove(row)
        return len(self._rows)

    def with_for_update(self):
        return self


ALL_TABLES = {}


def _make_model(name, fields):
    cls = type(name, (), {})
    for field in fields:
        setattr(cls, field, _Col(field))
    ALL_TABLES[cls] = []

    class _QueryDescriptor:
        def __get__(self, _obj, _owner):
            return _FakeQuery(ALL_TABLES[cls])

    cls.query = _QueryDescriptor()

    def _init(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        ALL_TABLES[cls].append(self)

    cls.__init__ = _init
    return cls


class _FakeSession:
    added = []

    @staticmethod
    def add(obj):
        table = ALL_TABLES.setdefault(type(obj), [])
        if obj not in table:
            table.append(obj)

    @staticmethod
    def commit():
        pass


class _FakeDb:
    session = _FakeSession


def _install_stubs():
    ctfd = types.ModuleType("CTFd")
    ctfd_models = types.ModuleType("CTFd.models")

    Challenges = _make_model("Challenges", ("id", "category", "state"))
    GameStage = _make_model("GameStage", ("id", "slug", "name", "category", "state", "expected_challenge_count"))
    GameStageChallenge = _make_model("GameStageChallenge", ("id", "stage_id", "challenge_id"))
    GameStageAudit = _make_model("GameStageAudit", ("id", "stage_id", "admin_id", "action", "details"))
    Solves = _make_model("Solves", ())
    Teams = _make_model("Teams", ())
    Users = _make_model("Users", ())

    ctfd_models.db = _FakeDb
    ctfd_models.Challenges = Challenges
    ctfd_models.Solves = Solves
    ctfd_models.Teams = Teams
    ctfd_models.Users = Users

    ctfd_plugins = types.ModuleType("CTFd.plugins")
    ctfd_plugins.bypass_csrf_protection = lambda f: f

    ctfd_utils = types.ModuleType("CTFd.utils")
    ctfd_utils.get_config = lambda *_a, **_kw: None
    ctfd_decorators = types.ModuleType("CTFd.utils.decorators")
    # Marker-setting rather than bare identity: every behavioral test in this
    # suite runs with these stubbed, so none of them can tell "correctly
    # authed" from "decorator deleted". The table-driven contract test at the
    # bottom of this file walks the blueprint's real url_map and asserts each
    # rule carries the marker its decorator implies, so that gap is closed in
    # CI rather than resting on code review. They still call straight
    # through, so behavior is unchanged.
    def _marked_admins_only(func):
        setattr(func, "__admins_only__", True)
        return func

    def _marked_authed_only(func):
        setattr(func, "__authed__", True)
        return func

    ctfd_decorators.admins_only = _marked_admins_only
    ctfd_decorators.authed_only = _marked_authed_only
    ctfd_user = types.ModuleType("CTFd.utils.user")
    def _raise_outside_request_context():
        # Matches real CTFd/Flask: get_current_user() touches the session
        # proxy, which raises RuntimeError outside an HTTP request context
        # rather than just returning None. reconcile_all_pending() is called
        # from load() at app startup -- no request context exists there --
        # so the stub must raise here too, or this exact bug (a crash on
        # every container start) can't be caught by these tests.
        raise RuntimeError("Working outside of request context.")

    ctfd_user.get_current_user = _raise_outside_request_context
    ctfd_user.is_admin = lambda: True

    # flask is NOT stubbed: it's a real dependency here (in production the
    # CTFd base image supplies it, see docker/ctfd/Dockerfile; for CI see
    # .github/workflows/build-ctfd.yml), so routes.py's blueprint is a real
    # Flask Blueprint and can be registered on a real app. That's what lets
    # the access-control contract test at the bottom walk the actual url_map
    # instead of trusting the source order of the decorators.
    for mod_name, mod in {
        "CTFd": ctfd,
        "CTFd.models": ctfd_models,
        "CTFd.plugins": ctfd_plugins,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.decorators": ctfd_decorators,
        "CTFd.utils.user": ctfd_user,
    }.items():
        sys.modules[mod_name] = mod

    return GameStage, GameStageChallenge, GameStageAudit, Challenges


GameStage, GameStageChallenge, GameStageAudit, Challenges = _install_stubs()

MODULE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(MODULE_DIR.parent))  # so `from .models import ...`-style relative imports resolve as a package

# Load models.py first under the real package name so routes.py's relative
# imports (`from .models import ...`, `from .logic import ...`) resolve.
import importlib
wargame_stages_pkg = types.ModuleType("wargame_stages")
wargame_stages_pkg.__path__ = [str(MODULE_DIR)]
sys.modules["wargame_stages"] = wargame_stages_pkg

models_spec = importlib.util.spec_from_file_location("wargame_stages.models", MODULE_DIR / "models.py")
models_mod = importlib.util.module_from_spec(models_spec)
# models.py defines its own GameStage/GameStageChallenge/GameStageAudit as
# real db.Model subclasses -- but we want routes.py to use OUR fakes instead
# (they're already registered under CTFd.models-style tables above), so
# monkeypatch models.py's exports post-import rather than executing its
# db.Model class bodies against our fake db.
sys.modules["wargame_stages.models"] = types.SimpleNamespace(
    GameStage=GameStage, GameStageChallenge=GameStageChallenge, GameStageAudit=GameStageAudit,
)

logic_spec = importlib.util.spec_from_file_location("wargame_stages.logic", MODULE_DIR / "logic.py")
logic_mod = importlib.util.module_from_spec(logic_spec)
logic_spec.loader.exec_module(logic_mod)
sys.modules["wargame_stages.logic"] = logic_mod

routes_spec = importlib.util.spec_from_file_location("wargame_stages.routes", MODULE_DIR / "routes.py")
routes_mod = importlib.util.module_from_spec(routes_spec)
routes_spec.loader.exec_module(routes_mod)


@pytest.fixture(autouse=True)
def _clean_tables():
    for table in ALL_TABLES.values():
        table.clear()
    yield


def _stage(slug, category, state="pending", expected_challenge_count=0):
    # expected_challenge_count must be set explicitly: any field not passed
    # as a kwarg here falls back to the class-level _Col("...") descriptor
    # (a mock column stand-in, not an int) rather than a real value, which
    # then fails to JSON-serialize inside _audit()'s json.dumps() call.
    return GameStage(
        id=len(ALL_TABLES[GameStage]) + 1,
        slug=slug,
        name=slug,
        category=category,
        state=state,
        expected_challenge_count=expected_challenge_count,
    )


def _challenge(category, state="visible"):
    return Challenges(id=len(ALL_TABLES[Challenges]) + 1, category=category, state=state)


class TestReconcileStage:
    def test_pending_stage_maps_and_hides_its_category(self):
        stage = _stage("bandit", "Linux Basics")
        c1 = _challenge("Linux Basics")
        c2 = _challenge("Linux Basics")
        other = _challenge("Cryptography")  # different category, must be untouched

        mapped = routes_mod._reconcile_stage(stage)

        assert mapped == 2
        assert c1.state == "hidden"
        assert c2.state == "hidden"
        assert other.state == "visible"
        mapped_ids = {row.challenge_id for row in ALL_TABLES[GameStageChallenge]}
        assert mapped_ids == {c1.id, c2.id}

    def test_started_stage_is_a_no_op(self):
        stage = _stage("bandit", "Linux Basics", state="active")
        c1 = _challenge("Linux Basics", state="visible")

        mapped = routes_mod._reconcile_stage(stage)

        assert mapped == 0
        assert c1.state == "visible"  # untouched, not force-hidden after start
        assert ALL_TABLES[GameStageChallenge] == []

    def test_challenge_already_mapped_to_another_stage_is_skipped_not_errored(self):
        other_stage = _stage("krypton", "Cryptography")
        stage = _stage("bandit", "Linux Basics")
        conflicted = _challenge("Linux Basics")
        GameStageChallenge(id=1, stage_id=other_stage.id, challenge_id=conflicted.id)
        clean = _challenge("Linux Basics")

        mapped = routes_mod._reconcile_stage(stage)

        assert mapped == 1  # only `clean`, not the conflicted one
        assert conflicted.state == "visible"  # left alone, not force-hidden into a stage it doesn't belong to
        assert clean.state == "hidden"

    def test_every_challenge_cross_mapped_to_another_stage_leaves_this_stage_intact(self):
        """The all-conflict case, which the one-conflict case above misses.

        When every challenge in the stage's category already belongs to a
        DIFFERENT stage, this stage has nothing to add -- but it must not
        destroy what it already had either. `own_ids` is empty there, so the
        delete's `~challenge_id.in_(own_ids)` clause has nothing to exclude and
        used to degrade to `.filter(True)`, wiping every mapping for this
        stage. The stage would then report 0 mapped and be permanently
        unstartable (start() aborts 409 on the count check) until someone
        edited the database by hand -- and this runs unattended at app startup
        and after every content push, so it could happen to any deployment
        whose categories overlap.
        """
        other_stage = _stage("krypton", "Cryptography")
        stage = _stage("bandit", "Linux Basics")
        c1 = _challenge("Linux Basics")
        c2 = _challenge("Linux Basics")
        GameStageChallenge(id=1, stage_id=other_stage.id, challenge_id=c1.id)
        GameStageChallenge(id=2, stage_id=other_stage.id, challenge_id=c2.id)
        # This stage's own prior (now stale but not ours to destroy) mapping.
        GameStageChallenge(id=3, stage_id=stage.id, challenge_id=c1.id)

        mapped = routes_mod._reconcile_stage(stage)

        assert mapped == 0
        surviving = {
            (row.stage_id, row.challenge_id)
            for row in ALL_TABLES[GameStageChallenge]
        }
        assert (stage.id, c1.id) in surviving, "reconcile wiped this stage's own mappings"
        assert (other_stage.id, c1.id) in surviving and (other_stage.id, c2.id) in surviving
        # The other stage's challenges stay visible: it owns them, not us.
        assert c1.state == "visible" and c2.state == "visible"

    def test_all_conflict_stage_reconcile_is_idempotent(self):
        """Re-running the unattended path (app restart, second content push)
        must not shrink a stage further each time."""
        other_stage = _stage("krypton", "Cryptography")
        stage = _stage("bandit", "Linux Basics")
        c1 = _challenge("Linux Basics")
        GameStageChallenge(id=1, stage_id=other_stage.id, challenge_id=c1.id)
        GameStageChallenge(id=2, stage_id=stage.id, challenge_id=c1.id)

        assert routes_mod._reconcile_stage(stage) == 0
        after_first = len(ALL_TABLES[GameStageChallenge])
        assert routes_mod._reconcile_stage(stage) == 0

        assert len(ALL_TABLES[GameStageChallenge]) == after_first

    def test_stage_with_no_challenges_at_all_still_clears_stale_mappings(self):
        """The OTHER empty-`own_ids` case: the category has no challenges in
        CTFd, so any mapping this stage still holds is stale and should be
        cleaned up. Guarding the delete must not have turned this into a
        no-op that leaves a stage permanently unstartable too."""
        stage = _stage("bandit", "Linux Basics")
        stale = _challenge("Cryptography")  # moved out of this category
        GameStageChallenge(id=1, stage_id=stage.id, challenge_id=stale.id)

        mapped = routes_mod._reconcile_stage(stage)

        assert mapped == 0
        assert ALL_TABLES[GameStageChallenge] == []

    def test_reconcile_all_pending_only_touches_pending_stages(self):
        pending = _stage("bandit", "Linux Basics")
        started = _stage("natas", "Web Security", state="active")
        pending_challenge = _challenge("Linux Basics")
        started_challenge = _challenge("Web Security")

        summary = routes_mod.reconcile_all_pending()

        # Only pending stages are iterated at all (see reconcile_all_pending's
        # docstring) -- started stages get no summary entry, not a 0 entry.
        assert summary == {"bandit": 1}
        assert pending_challenge.state == "hidden"
        assert started_challenge.state == "visible"


# ── /machine/reconcile shared-secret auth ─────────────────────────────────

SECRET = "shared-secret-value"


@pytest.fixture
def reconcile_client(monkeypatch):
    """A real Flask test client for the blueprint's /machine/reconcile route.

    X-Sync-Auth is compared with hmac.compare_digest, which raises TypeError
    on `str` operands containing any byte >= 0x80 -- so before this was
    encoded on both sides, a request carrying a single non-ASCII byte in the
    header produced an unhandled 500 on a machine-to-machine endpoint that
    must never do anything but 401. Flask/Werkzeug passes header values
    through as latin-1-decoded str, so a raw 0x80-0xFF byte on the wire lands
    in the request exactly as a non-ASCII str.
    """
    from flask import Flask

    monkeypatch.setattr(routes_mod, "read_secret", lambda _name: SECRET)
    app = Flask(__name__, static_folder=None)
    app.register_blueprint(routes_mod.wargame_stages_bp)
    return app.test_client()


def test_machine_reconcile_without_the_header_is_401(reconcile_client):
    assert reconcile_client.post("/plugins/wargame-stages/machine/reconcile").status_code == 401


def test_machine_reconcile_with_a_wrong_secret_is_401(reconcile_client):
    resp = reconcile_client.post(
        "/plugins/wargame-stages/machine/reconcile", headers={"X-Sync-Auth": "not-the-secret"}
    )
    assert resp.status_code == 401


def test_machine_reconcile_with_the_right_secret_is_200(reconcile_client):
    stage = _stage("bandit", "Linux Basics")
    _challenge("Linux Basics")

    resp = reconcile_client.post(
        "/plugins/wargame-stages/machine/reconcile", headers={"X-Sync-Auth": SECRET}
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"bandit": 1}
    assert len(ALL_TABLES[GameStageChallenge]) == 1


def test_machine_reconcile_with_an_empty_configured_secret_is_401(reconcile_client, monkeypatch):
    """Fail closed when the deployment never provisioned the secret, rather
    than letting any caller through."""
    monkeypatch.setattr(routes_mod, "read_secret", lambda _name: "")
    resp = reconcile_client.post(
        "/plugins/wargame-stages/machine/reconcile", headers={"X-Sync-Auth": ""}
    )
    assert resp.status_code == 401


def test_machine_reconcile_with_a_non_ascii_header_is_401_not_500(reconcile_client):
    """The regression: hmac.compare_digest(str, str) raises TypeError for any
    operand with a byte >= 0x80, which surfaced as a 500 on both machine
    endpoints instead of the 401 the request deserves."""
    resp = reconcile_client.post(
        "/plugins/wargame-stages/machine/reconcile",
        headers={"X-Sync-Auth": "café-" + SECRET},
    )
    assert resp.status_code == 401, f"expected a clean 401, got {resp.status_code}"


# ── route access-control contract ─────────────────────────────────────────
# Every behavioral test above runs with `admins_only`/`authed_only` stubbed to
# mark-and-call-through, so none of them can distinguish "correctly authed"
# from "decorator deleted". This table-driven test closes that gap by walking
# the blueprint's real url_map and asserting each rule carries the marker its
# decorator implies -- so dropping an admin guard now fails CI instead of
# resting on code review alone.

EXPECTED_AUTH = {
    "wargame_stages.overview": {"authed": True, "admins": False},
    "wargame_stages.scoreboard": {"authed": True, "admins": False},
    "wargame_stages.admin": {"authed": False, "admins": True},
    "wargame_stages.export": {"authed": False, "admins": True},
    "wargame_stages.sync": {"authed": False, "admins": True},
    # Machine-to-machine: authenticated by X-Sync-Auth, not a CTFd session.
    "wargame_stages.machine_reconcile": {"authed": False, "admins": False},
    "wargame_stages.start": {"authed": False, "admins": True},
    "wargame_stages.lock": {"authed": False, "admins": True},
    "wargame_stages.close": {"authed": False, "admins": True},
    "wargame_stages.visibility": {"authed": False, "admins": True},
}


@pytest.mark.parametrize("endpoint,expected", sorted(EXPECTED_AUTH.items()))
def test_every_route_carries_the_expected_auth_decorator(endpoint, expected):
    from flask import Flask

    app = Flask(__name__, static_folder=None)
    app.register_blueprint(routes_mod.wargame_stages_bp)
    view = app.view_functions[endpoint]
    assert getattr(view, "__authed__", False) is expected["authed"]
    assert getattr(view, "__admins_only__", False) is expected["admins"]


def test_url_map_has_no_routes_beyond_the_ones_this_test_asserts():
    """A new route added without updating EXPECTED_AUTH would otherwise go
    unasserted -- make that a deliberate edit here."""
    from flask import Flask

    app = Flask(__name__, static_folder=None)
    app.register_blueprint(routes_mod.wargame_stages_bp)
    assert {rule.endpoint for rule in app.url_map.iter_rules()} == set(EXPECTED_AUTH)
