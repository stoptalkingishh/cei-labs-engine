"""docker/ctfd/plugins/instance-launcher/solve_hook.py

Detects a correct flag submission and starts the post-solve shutdown
countdown on the participant's instance — but only once every challenge
sharing that instance (see InstanceChallengeConfig.instance_group) has been
solved. A boot2root box with three flags at different privilege levels, all
configured with the same instance_group, won't shut down after the first
flag — only after the last one, so the participant doesn't lose their
environment mid-box. instance_group is optional; a challenge with none is
its own one-challenge group.

Per-challenge shutdown_on_solve=False opts a challenge out of triggering the
countdown entirely (it can still be a member of a group gating other
challenges' shutdown, its own solve just never itself starts the countdown).

CTFd's flag-submission endpoint is POST /api/v1/challenges/attempt, with a
request body of {"challenge_id": ..., "submission": ...} and a response body
of {"success": true, "data": {"status": "correct"|"incorrect"|...}} — this
is CTFd's long-standing, documented API contract (the same one
CTFd/plugins/challenges/assets/view.js itself calls via
CTFd.api.post_challenge_attempt). Hooking it at the Flask after_request
layer, rather than overriding a challenge type's solve() classmethod, means
this works for every challenge regardless of type and needs no CTFd
internals beyond that one stable endpoint contract.

This is best-effort: any failure to reach the orchestrator is logged and
swallowed rather than surfaced to the participant — a flag submission must
never fail because the orchestrator is briefly unreachable.
"""
import logging

from flask import request

from CTFd.models import Solves
from CTFd.utils.user import get_current_user

from .models import InstanceChallengeConfig
from .orchestrator_client import OrchestratorClient, OrchestratorError
from .shutdown_gate import gate_satisfied

logger = logging.getLogger(__name__)

ATTEMPT_PATH_SUFFIX = "/api/v1/challenges/attempt"


def register(app) -> None:
    @app.after_request
    def _detect_solve_and_schedule_shutdown(response):
        try:
            _maybe_schedule_shutdown(response)
        except Exception:
            logger.exception("solve-hook failed (non-fatal, response is unaffected)")
        return response


def _maybe_schedule_shutdown(response) -> None:
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

    config = InstanceChallengeConfig.query.filter_by(challenge_id=challenge_id).first()
    if config is None or not config.shutdown_on_solve:
        return

    user = get_current_user()
    if user is None:
        return
    owner_id = str(user.account_id)

    # Every OTHER challenge sharing this instance_group that also wants to
    # gate on solve must be solved too before the shared environment goes
    # down. A solo challenge (no group) gates on just itself.
    if config.instance_group:
        siblings = InstanceChallengeConfig.query.filter_by(instance_group=config.instance_group, shutdown_on_solve=True).all()
    else:
        siblings = [config]
    gate_challenge_ids = {c.challenge_id for c in siblings}

    solved_rows = Solves.query.filter(Solves.challenge_id.in_(gate_challenge_ids)).all()
    # Checked per-row (real column values), not as a query filter — avoids
    # depending on account_id's hybrid_property being usable as a SQL
    # expression, which isn't exercised by this plugin's test suite (no live
    # CTFd/DB available there).
    solved_challenge_ids = {row.challenge_id for row in solved_rows if str(row.account_id) == owner_id}

    if not gate_satisfied(gate_challenge_ids, solved_challenge_ids):
        return

    instance_key = config.resolved_instance_key()
    try:
        OrchestratorClient.from_env().schedule_shutdown(owner_id, instance_key)
    except OrchestratorError:
        logger.warning("could not schedule shutdown for owner=%s key=%s", owner_id, instance_key, exc_info=True)
