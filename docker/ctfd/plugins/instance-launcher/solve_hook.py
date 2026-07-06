"""docker/ctfd/plugins/instance-launcher/solve_hook.py

Detects a correct flag submission and starts the post-solve shutdown
countdown on the participant's instance (if that challenge has one).

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

from CTFd.utils.user import get_current_user

from .models import InstanceChallengeConfig
from .orchestrator_client import OrchestratorClient, OrchestratorError

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
    if config is None:
        return

    user = get_current_user()
    if user is None:
        return

    owner_id = str(user.account_id)
    instance_key = f"challenge-{challenge_id}"

    try:
        OrchestratorClient.from_env().schedule_shutdown(owner_id, instance_key)
    except OrchestratorError:
        logger.warning("could not schedule shutdown for owner=%s key=%s", owner_id, instance_key, exc_info=True)
