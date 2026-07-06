"""docker/ctfd/plugins/instance-launcher/routes.py

Three audiences, three route groups:
  - /launch/<id>            participant, session-authed (CTFd login)
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
    config = InstanceChallengeConfig.query.filter_by(challenge_id=challenge_id).first()
    if config is None:
        abort(404, description="This challenge has no environment configured.")

    user = get_current_user()
    owner_id = str(user.account_id)
    instance_key = config.resolved_instance_key()
    client = OrchestratorClient.from_env()
    action = request.form.get("action") if request.method == "POST" else None

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

    return render_template(
        "instance_launcher/launch.html",
        challenge=challenge,
        config=config,
        status=status,
        error=error,
    )


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
def sync_mapping():
    """Non-interactive upsert for scripts/challenges-load.sh — matches by
    challenge NAME (not id) since ctfcli, not this plugin, is what creates
    challenges from YAML in the first place."""
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
