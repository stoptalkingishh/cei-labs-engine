"""docker/ctfd/plugins/submission-history/routes.py

Three routes, all participant-facing, all `@authed_only`, all read-only, and
all scoped strictly to the requesting account's OWN `Solves` rows -- never
another team's or user's:

  - /api/solve/<id>   the flag text this account submitted for a specific
                      challenge, if (and only if) this account has actually
                      solved it. Used by submission-history.js's reveal
                      button in the challenge modal.
  - /api/solves       every challenge this account has solved, each with its
                      submitted flag -- the JSON backing /solves below, and
                      the more useful shape for the actual motivating case
                      (looking up an old Bandit/Krypton/Natas password
                      without having to reopen each challenge individually).
  - /solves           a plain HTML page rendering /api/solves' data, reached
                      directly (bookmarkable, works even if the injected
                      modal script never loads) -- the same
                      "full page as a fallback for the injected JS" shape
                      instance-launcher/routes.py's /launch/<id> already
                      uses next to its own /api/status, /api/launch.

Scoping: CTFd's `Solves` table (CTFd/models/__init__.py) has separate
`user_id` and `team_id` columns, not a single unified `account_id` column --
which of the two is "this account" depends on the deployment's
`user_mode` config. This follows the exact team_mode-aware pattern
wargame-stages/routes.py's `_scoreboard()` already uses
(`Solves.team_id if team_mode else Solves.user_id`), rather than the
simpler `str(get_current_user().account_id)` convention hint-wallet/
instance-launcher use for THEIR OWN plugin-owned tables (which only ever
have a single `owner_id` column to begin with) -- Solves is a CTFd core
table with two, so the query has to pick the right one explicitly.
"""
from flask import Blueprint, jsonify, render_template

from CTFd.models import Challenges, Solves
from CTFd.utils import get_config
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_user

submission_history_bp = Blueprint(
    "submission_history",
    __name__,
    template_folder="templates",
    url_prefix="/plugins/submission-history",
)


def _account_column():
    """Solves.team_id in team mode, Solves.user_id in user mode -- matches
    wargame-stages/routes.py's _scoreboard() exactly, so this plugin and
    wargame-stages agree on what "this account's own solves" means."""
    team_mode = get_config("user_mode") == "teams"
    return Solves.team_id if team_mode else Solves.user_id


def _current_account_id():
    user = get_current_user()
    return user.account_id


def _own_solves_query():
    """Every Solves row belonging to the CURRENT account only -- the one
    filter every route below must apply before touching `provided`."""
    return Solves.query.filter(_account_column() == _current_account_id())


def _own_solve_rows():
    """(challenge_id, challenge_name, provided, date) for this account's own
    solves, newest first -- shared by /api/solves and /solves so the two
    can't drift apart on what "own solves" means."""
    return (
        _own_solves_query()
        .join(Challenges, Challenges.id == Solves.challenge_id)
        .add_columns(Challenges.name)
        .order_by(Solves.date.desc())
        .all()
    )


def _serialize(solve, challenge_name):
    return {
        "challenge_id": solve.challenge_id,
        "challenge_name": challenge_name,
        "provided": solve.provided,
        "date": solve.date.isoformat() if solve.date else None,
    }


@submission_history_bp.route("/api/solve/<int:challenge_id>", methods=["GET"])
@authed_only
def api_solve(challenge_id: int):
    # Same join as _own_solve_rows() (not solve.challenge) -- avoids
    # depending on a Solves->Challenges relationship/backref name that isn't
    # actually used anywhere else in this codebase's plugins.
    row = (
        _own_solves_query()
        .filter(Solves.challenge_id == challenge_id)
        .join(Challenges, Challenges.id == Solves.challenge_id)
        .add_columns(Challenges.name)
        .first()
    )
    if row is None:
        # Deliberately the SAME 404 whether this account never solved the
        # challenge or the challenge doesn't exist at all -- never leaks
        # whether some OTHER account has solved it.
        return jsonify(error="not_solved"), 404
    solve, challenge_name = row
    return jsonify(_serialize(solve, challenge_name)), 200


@submission_history_bp.route("/api/solves", methods=["GET"])
@authed_only
def api_solves():
    rows = _own_solve_rows()
    return jsonify(solves=[_serialize(solve, name) for solve, name in rows]), 200


@submission_history_bp.route("/solves", methods=["GET"])
@authed_only
def solves_page():
    rows = _own_solve_rows()
    return render_template("submission_history/solves.html", solves=rows)
