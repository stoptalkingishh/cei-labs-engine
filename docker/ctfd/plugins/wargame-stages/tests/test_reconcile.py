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
    ctfd_decorators.admins_only = lambda f: f
    ctfd_decorators.authed_only = lambda f: f
    ctfd_user = types.ModuleType("CTFd.utils.user")
    ctfd_user.get_current_user = lambda: None
    ctfd_user.is_admin = lambda: True

    flask_stub = types.ModuleType("flask")
    for name in ("Blueprint", "Response", "abort", "flash", "jsonify", "redirect", "render_template", "request", "url_for"):
        setattr(flask_stub, name, lambda *a, **kw: None)
    flask_stub.Blueprint = lambda *a, **kw: types.SimpleNamespace(route=lambda *a2, **kw2: (lambda f: f))

    for mod_name, mod in {
        "CTFd": ctfd,
        "CTFd.models": ctfd_models,
        "CTFd.plugins": ctfd_plugins,
        "CTFd.utils": ctfd_utils,
        "CTFd.utils.decorators": ctfd_decorators,
        "CTFd.utils.user": ctfd_user,
        "flask": flask_stub,
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


def _stage(slug, category, state="pending"):
    return GameStage(id=len(ALL_TABLES[GameStage]) + 1, slug=slug, name=slug, category=category, state=state)


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

    def test_reconcile_all_pending_only_touches_pending_stages(self):
        pending = _stage("bandit", "Linux Basics")
        started = _stage("natas", "Web Security", state="active")
        pending_challenge = _challenge("Linux Basics")
        started_challenge = _challenge("Web Security")

        summary = routes_mod.reconcile_all_pending()

        assert summary == {"bandit": 1, "natas": 0}
        assert pending_challenge.state == "hidden"
        assert started_challenge.state == "visible"
