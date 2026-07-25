"""docker/ctfd/plugins/hint-wallet/solve_hook.py

Detects a correct flag submission and, if the solving owner had opened a
hint tier for that specific challenge, reduces THAT challenge's own score
award by the tier's percent cost (cei-labs-event#7). This is the "your
final score on this challenge is reduced by having peeked" model, not a
spend from a shared team-currency wallet -- there is no such wallet
anywhere in this system anymore (see orchestrator_client.py and the
orchestrator's app/store.py WalletStore docstring).

Hooked at the Flask after_request layer on POST /api/v1/challenges/attempt,
matched by challenge_id, following the EXACT same pattern
instance-launcher/solve_hook.py already uses in this repo (see that file's
docstring for why: this is CTFd's stable, documented API contract, works
for every challenge type, and needs no CTFd internals beyond that one
endpoint). Like instance-launcher's hook, this is best-effort: any failure
here is logged and swallowed, never surfaced to the participant -- a flag
submission must never fail because this plugin (or the orchestrator) is
briefly unreachable.

Score reduction mechanism: a compensating negative CTFd `Award` for this
user/team, scoped to this one challenge. CTFd's scoreboard sums a team's
challenge.value awards plus any Awards rows, so a negative Award is the
standard way to apply a per-solve deduction without mutating the
challenge's own global `value` (which is shared by every team) or
overriding CTFd's own Solve-handling internals. Guarded idempotent by
looking for an already-created Award with this exact (account, challenge)
description tag before inserting another one -- important because this
hook can, in principle, observe more than one "correct" response for the
same challenge_id/account (e.g. a retried request) even though CTFd's own
solve flow normally only returns "correct" once.

IMPORTANT (see wargame-stages/routes.py's `_admin_id()` postmortem,
referenced in cei-labs-event#7): `get_current_user()` raises RuntimeError
outside a real HTTP request context. This hook only ever runs inside
`after_request`, which is always within a request context, so that failure
mode does not apply here -- but the outer try/except in `register()` below
still catches it (and everything else) defensively, matching
instance-launcher/solve_hook.py's own registration pattern.
"""
import logging

from flask import request

from CTFd.models import Awards, Challenges, db
from CTFd.utils.user import get_current_user

from .orchestrator_client import OrchestratorClient, OrchestratorError
from .track_mapping import track_for_category

logger = logging.getLogger(__name__)

ATTEMPT_PATH_SUFFIX = "/api/v1/challenges/attempt"
AWARD_CATEGORY = "hint-wallet-penalty"


def register(app) -> None:
    @app.after_request
    def _detect_solve_and_apply_hint_penalty(response):
        try:
            _maybe_apply_hint_penalty(response)
        except Exception:
            logger.exception("hint-wallet solve-hook failed (non-fatal, response is unaffected)")
        return response


def _award_description(challenge_id) -> str:
    """Stable per-(account, challenge) tag used both to write and to check
    for an existing penalty Award -- see this module's docstring on why
    idempotency matters here."""
    return f"hint-wallet-penalty:challenge:{challenge_id}"


def _maybe_apply_hint_penalty(response) -> None:
    if request.method != "POST" or not request.path.endswith(ATTEMPT_PATH_SUFFIX):
        return
    if response.status_code != 200:
        return

    payload = response.get_json(silent=True)
    if not payload or not payload.get("success"):
        return
    if (payload.get("data") or {}).get("status") != "correct":
        return

    body = request.get_json(silent=True) or {}
    challenge_id = body.get("challenge_id")
    if not challenge_id:
        return

    challenge = Challenges.query.filter_by(id=challenge_id).first()
    if challenge is None:
        return

    user = get_current_user()
    if user is None:
        return
    owner_id = str(user.account_id)

    _apply_penalty(owner_id, user, challenge, _award_description(challenge_id))


def _apply_penalty(owner_id: str, user, challenge, description: str) -> None:
    track = track_for_category(challenge.category)
    if track is None:
        return  # not a Bandit/Krypton/Natas challenge -- no hint-wallet entry can exist
    entry_name = challenge.name

    client = OrchestratorClient.from_env()
    try:
        unlocked = client.unlocked(owner_id, track, entry_name)
    except OrchestratorError:
        logger.warning(
            "hint-wallet solve-hook: could not reach orchestrator to check hint usage for "
            "owner=%s challenge_id=%s -- no penalty applied this time",
            owner_id, challenge.id, exc_info=True,
        )
        return

    tier = unlocked.get("tier")
    cost_percent = unlocked.get("cost_percent")
    if not tier or not cost_percent:
        return  # never opened a hint for this challenge -- full value stands

    penalty = (int(challenge.value or 0) * int(cost_percent)) // 100
    if penalty <= 0:
        return

    # Idempotency: a retried/duplicated "correct" observation for the same
    # account+challenge must never apply the penalty twice.
    existing = Awards.query.filter_by(
        user_id=getattr(user, "id", None), description=description
    ).first()
    if existing is not None:
        return

    award = Awards(
        user_id=getattr(user, "id", None),
        team_id=getattr(user, "team_id", None),
        name=f"Hint penalty ({cost_percent}% of {challenge.name})",
        description=description,
        value=-penalty,
        category=AWARD_CATEGORY,
    )
    db.session.add(award)
    db.session.commit()
