"""docker/ctfd/plugins/instance-launcher/routes.py

Four audiences, four route groups:
  - /launch/<id>            participant, session-authed (CTFd login) — the
                            original full-page HTML flow. Kept working
                            unmodified as a fallback in case the injected
                            challenge-modal JS (challenge-launch.js) ever
                            fails to load — a direct link/bookmark still
                            works even then.
  - /api/status/<id>,
    /api/launch/<id>         participant, session-authed — JSON versions of
                            the same actions, driven by challenge-launch.js
                            from inside CTFd's own challenge modal so a
                            player never has to navigate away to launch or
                            check on their environment. Share their actual
                            logic with /launch/<id> via _resolve_config()
                            and _run_action() below, not duplicated.
  - /admin/mappings         admin, session-authed (CTFd admin login)
  - /admin/mappings/sync    automation (scripts/challenges-load.sh), authed
                            with the same plugin_shared_secret Docker secret
                            used for the CTFd<->orchestrator call — avoids
                            depending on CTFd's own admin-token internals for
                            a script that already has filesystem access to
                            that secret.
"""
import hmac

from flask import Blueprint, abort, redirect, render_template, request, url_for

from CTFd.models import Challenges, db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_user

from .models import InstanceChallengeConfig
from .orchestrator_client import OrchestratorClient, OrchestratorError, read_secret

instance_launcher_bp = Blueprint(
    "instance_launcher",
    __name__,
    template_folder="templates",
    url_prefix="/plugins/instance-launcher",
)

VALID_TYPES = ("web-app", "single-target", "target-attacker")


def _resolve_config(challenge_id: int) -> "InstanceChallengeConfig | None":
    return InstanceChallengeConfig.query.filter_by(challenge_id=challenge_id).first()


def _run_action(config: InstanceChallengeConfig, action: "str | None"):
    """Shared by the HTML /launch/<id> route and the JSON /api/launch/<id>
    route -- one implementation of the actual four actions, two different
    response formats on top of it.

    Returns (status: dict|None, error: str|None).
    """
    user = get_current_user()
    owner_id = str(user.account_id)
    instance_key = config.resolved_instance_key()
    client = OrchestratorClient.from_env()

    error = None
    try:
        if action == "reboot":
            client.reboot(owner_id, instance_key)
        elif action == "relaunch":
            client.create_or_get(config.instance_type, owner_id, instance_key, config.to_orchestrator_spec(), relaunch=True)
        elif action == "extend":
            client.extend_shutdown(owner_id, instance_key)
        else:
            client.create_or_get(config.instance_type, owner_id, instance_key, config.to_orchestrator_spec())
    except OrchestratorError as exc:
        error = str(exc)

    status = None
    if error is None:
        try:
            status = client.get(owner_id, instance_key)
        except OrchestratorError as exc:
            error = str(exc)

    return status, error


@instance_launcher_bp.route("/launch/<int:challenge_id>", methods=["GET", "POST"])
@authed_only
def launch(challenge_id: int):
    """Three actions, one page:
      - (GET, or POST with no action) "Launch Environment" — create-or-get
      - POST action=reboot   "Reboot Host" — restart in place
      - POST action=relaunch "Relaunch Environment" — destroy + recreate fresh
      - POST action=extend   "+5 more minutes" during a post-solve countdown
    """
    challenge = Challenges.query.get_or_404(challenge_id)
    config = _resolve_config(challenge_id)
    if config is None:
        abort(404, description="This challenge has no environment configured.")

    action = request.form.get("action") if request.method == "POST" else None
    status, error = _run_action(config, action)

    return render_template(
        "instance_launcher/launch.html",
        challenge=challenge,
        config=config,
        status=status,
        error=error,
    )


@instance_launcher_bp.route("/api/status/<int:challenge_id>", methods=["GET"])
@authed_only
def api_status(challenge_id: int):
    """Pure read -- never creates or mutates anything, safe to poll
    repeatedly. Used by challenge-launch.js to decide whether a challenge
    has a launchable environment at all, and to poll live status once a
    player has launched one, without ever re-triggering create_or_get."""
    config = _resolve_config(challenge_id)
    if config is None:
        return {"has_environment": False}, 200

    user = get_current_user()
    owner_id = str(user.account_id)
    instance_key = config.resolved_instance_key()
    client = OrchestratorClient.from_env()

    try:
        status = client.get(owner_id, instance_key)
    except OrchestratorError as exc:
        return {"has_environment": True, "instance_type": config.instance_type, "instance_group": config.instance_group, "error": str(exc)}, 200

    return {
        "has_environment": True,
        "instance_type": config.instance_type,
        "instance_group": config.instance_group,
        "status": status,
    }, 200


@instance_launcher_bp.route("/api/launch/<int:challenge_id>", methods=["POST"])
@authed_only
def api_launch(challenge_id: int):
    """JSON twin of /launch/<id>'s POST actions -- same _run_action(), same
    four actions (default/reboot/relaunch/extend via {"action": ...} in the
    JSON body instead of a form field), for challenge-launch.js to call via
    fetch() without a full page navigation. Still behind CTFd's normal
    session + CSRF checks (@authed_only, no @bypass_csrf_protection) —
    challenge-launch.js sends CTFd's own CSRF-Token header, matching every
    other authenticated fetch() call CTFd's own bundled frontend makes."""
    config = _resolve_config(challenge_id)
    if config is None:
        abort(404, description="This challenge has no environment configured.")

    body = request.get_json(silent=True) or {}
    status, error = _run_action(config, body.get("action"))

    if error is not None:
        return {"success": False, "error": error}, 200
    return {"success": True, "status": status}, 200


@instance_launcher_bp.route("/admin/mappings", methods=["GET", "POST"])
@admins_only
def admin_mappings():
    if request.method == "POST":
        challenge_id = int(request.form["challenge_id"])
        instance_type = request.form["instance_type"]
        if instance_type not in VALID_TYPES:
            abort(400, description=f"instance_type must be one of {VALID_TYPES}")

        config = InstanceChallengeConfig.query.filter_by(challenge_id=challenge_id).first()
        if config is None:
            config = InstanceChallengeConfig(challenge_id=challenge_id)
            db.session.add(config)

        config.instance_type = instance_type
        config.image = request.form.get("image") or None
        config.port = int(request.form["port"]) if request.form.get("port") else None
        config.target_image = request.form.get("target_image") or None
        config.attacker_image = request.form.get("attacker_image") or None
        config.attacker_port = int(request.form["attacker_port"]) if request.form.get("attacker_port") else None
        config.instance_group = request.form.get("instance_group") or None
        config.shutdown_on_solve = request.form.get("shutdown_on_solve") == "on"
        db.session.commit()
        return redirect(url_for("instance_launcher.admin_mappings"))

    challenges = Challenges.query.order_by(Challenges.id).all()
    configs = {c.challenge_id: c for c in InstanceChallengeConfig.query.all()}
    return render_template("instance_launcher/admin_mappings.html", challenges=challenges, configs=configs)


@instance_launcher_bp.route("/admin/mappings/<int:challenge_id>/delete", methods=["POST"])
@admins_only
def admin_delete_mapping(challenge_id: int):
    InstanceChallengeConfig.query.filter_by(challenge_id=challenge_id).delete()
    db.session.commit()
    return redirect(url_for("instance_launcher.admin_mappings"))


@instance_launcher_bp.route("/admin/mappings/sync", methods=["POST"])
@bypass_csrf_protection
def sync_mapping():
    """Non-interactive upsert for scripts/challenges-load.sh — matches by
    challenge NAME (not id) since ctfcli, not this plugin, is what creates
    challenges from YAML in the first place. Needs @bypass_csrf_protection
    because this route is deliberately called without a CTFd session (the
    whole point is to work headlessly from a script) -- without it, CTFd's
    own global CSRF check rejects the request with 403 before this
    function's own X-Sync-Auth check ever runs."""
    provided = request.headers.get("X-Sync-Auth", "")
    expected = read_secret("plugin_shared_secret")
    if not expected or not hmac.compare_digest(provided, expected):
        abort(401)

    body = request.get_json(force=True, silent=True) or {}
    name = body.get("challenge_name")
    instance_type = body.get("instance_type")
    if not name or instance_type not in VALID_TYPES:
        abort(400, description=f"'challenge_name' is required and 'instance_type' must be one of {VALID_TYPES}")

    challenge = Challenges.query.filter_by(name=name).first()
    if challenge is None:
        abort(404, description=f"no challenge named {name!r}")

    config = InstanceChallengeConfig.query.filter_by(challenge_id=challenge.id).first()
    if config is None:
        config = InstanceChallengeConfig(challenge_id=challenge.id)
        db.session.add(config)

    config.instance_type = instance_type
    config.image = body.get("image")
    config.port = body.get("port")
    config.target_image = body.get("target_image")
    config.attacker_image = body.get("attacker_image")
    config.attacker_port = body.get("attacker_port")
    config.instance_group = body.get("instance_group")
    # Default true: absent/omitted in YAML means "shut down on solve", the
    # historical/expected behavior — only explicit `false` opts out.
    config.shutdown_on_solve = body.get("shutdown_on_solve", True)
    db.session.commit()
    return {"status": "synced", "challenge_id": challenge.id}, 200
