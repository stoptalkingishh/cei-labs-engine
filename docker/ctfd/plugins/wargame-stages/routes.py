import json
import csv
import hmac
import io
import os
from datetime import datetime

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_user, is_admin

from .logic import InvalidTransition, SolveEvent, close_stage, lock_stage, rank_solves, safe_csv_cell, start_stage
from .models import GameStage, GameStageAudit, GameStageChallenge

wargame_stages_bp = Blueprint("wargame_stages", __name__, template_folder="templates", url_prefix="/plugins/wargame-stages")


def read_secret(name: str) -> str:
    """Mirrors instance-launcher/hint-wallet's orchestrator_client.read_secret —
    duplicated rather than shared across plugins, matching the existing
    per-plugin convention in this codebase."""
    path = f"/run/secrets/{name}"
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name.upper(), "")


def _admin_id():
    user = get_current_user()
    return user.id if user else None


def _audit(stage, action, details=None):
    db.session.add(GameStageAudit(stage_id=stage.id, admin_id=_admin_id(), action=action, details=json.dumps(details or {})))


def _mapped_count(stage):
    return GameStageChallenge.query.filter_by(stage_id=stage.id).count()


def _scoreboard(stage):
    if stage.started_at is None:
        return []
    cutoff = stage.locked_at or stage.closed_at
    team_mode = get_config("user_mode") == "teams"
    account_column = Solves.team_id if team_mode else Solves.user_id
    account_model = Teams if team_mode else Users
    rows = (
        db.session.query(account_column, account_model.name, Challenges.value, Solves.date)
        .join(GameStageChallenge, GameStageChallenge.challenge_id == Solves.challenge_id)
        .join(Challenges, Challenges.id == Solves.challenge_id)
        .join(account_model, account_model.id == account_column)
        .filter(GameStageChallenge.stage_id == stage.id)
        .filter(account_column.isnot(None), account_model.hidden.is_(False), account_model.banned.is_(False))
        .all()
    )
    events = [SolveEvent(account_id, name, value, solved_at) for account_id, name, value, solved_at in rows]
    return rank_solves(events, stage.started_at, cutoff)


@wargame_stages_bp.route("/")
@authed_only
def overview():
    stages = GameStage.query.order_by(GameStage.display_order).all()
    return render_template("wargame_stages/overview.html", stages=stages)


@wargame_stages_bp.route("/<slug>/scoreboard")
@authed_only
def scoreboard(slug):
    stage = GameStage.query.filter_by(slug=slug).first_or_404()
    if not stage.scoreboard_visible and not is_admin():
        abort(404)
    return render_template("wargame_stages/scoreboard.html", stage=stage, rows=_scoreboard(stage))


@wargame_stages_bp.route("/admin")
@admins_only
def admin():
    stages = GameStage.query.order_by(GameStage.display_order).all()
    return render_template("wargame_stages/admin.html", stages=stages, mapped_counts={s.id: _mapped_count(s) for s in stages})


@wargame_stages_bp.route("/admin/<slug>/export.<format>")
@admins_only
def export(slug, format):
    stage = GameStage.query.filter_by(slug=slug).first_or_404()
    rows = _scoreboard(stage)
    metadata = {
        "slug": stage.slug, "name": stage.name, "state": stage.state,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "score_cutoff": (stage.locked_at or stage.closed_at).isoformat() if (stage.locked_at or stage.closed_at) else None,
        "exported_at": datetime.utcnow().isoformat(),
    }
    if format == "json":
        serializable = []
        for row in rows:
            item = dict(row)
            item["last_solve_at"] = item["last_solve_at"].isoformat()
            serializable.append(item)
        return jsonify({"stage": metadata, "standings": serializable})
    if format != "csv":
        abort(404)
    output = io.StringIO()
    fields = ("stage_slug", "stage_state", "started_at", "score_cutoff", "exported_at", "place", "account_id", "name", "score", "solve_count", "last_solve_at", "elapsed_seconds")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows or [{}]:
        item = dict(row)
        item.update({"stage_slug": stage.slug, "stage_state": stage.state, "started_at": metadata["started_at"], "score_cutoff": metadata["score_cutoff"], "exported_at": metadata["exported_at"]})
        if item.get("last_solve_at"):
            item["last_solve_at"] = item["last_solve_at"].isoformat()
        item = {key: safe_csv_cell(value) for key, value in item.items()}
        writer.writerow(item)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={stage.slug}-standings.csv"})


def _reconcile_stage(stage):
    """Map every challenge in `stage.category` to `stage` and force it hidden.

    A no-op for anything past `pending` — once a stage has started, challenge
    mappings and visibility are frozen (visibility becomes a manual "Start"
    decision instead). Challenges already mapped to a DIFFERENT stage are
    left alone (skipped, not an error) rather than aborting the whole
    reconcile over one bad mapping — this runs unattended (app startup, the
    deploy-time machine endpoint below), so it must degrade gracefully
    instead of taking down every other stage's reconciliation.

    Returns the number of challenges mapped into this stage.
    """
    if stage.state != "pending":
        return 0
    challenges = Challenges.query.filter_by(category=stage.category).order_by(Challenges.id).all()
    ids = {challenge.id for challenge in challenges}
    already_elsewhere = {
        row.challenge_id
        for row in GameStageChallenge.query.filter(
            GameStageChallenge.challenge_id.in_(ids), GameStageChallenge.stage_id != stage.id
        ).all()
    } if ids else set()
    own_ids = ids - already_elsewhere
    GameStageChallenge.query.filter_by(stage_id=stage.id).filter(~GameStageChallenge.challenge_id.in_(own_ids) if own_ids else True).delete(synchronize_session=False)
    existing = {row.challenge_id for row in GameStageChallenge.query.filter_by(stage_id=stage.id).all()}
    for challenge in challenges:
        if challenge.id in already_elsewhere:
            continue
        if challenge.id not in existing:
            db.session.add(GameStageChallenge(stage_id=stage.id, challenge_id=challenge.id))
        challenge.state = "hidden"
    return len(own_ids)


def reconcile_all_pending():
    """Hide-by-default enforcement for every stage that hasn't started yet.

    Called (a) at app startup via load(), covering container
    restarts/redeploys, and (b) from the /machine/reconcile endpoint, which
    CEI-Labs-Wargames/deploy.sh calls automatically after every content
    push — so newly-deployed challenges are hidden the moment they land,
    with no admin click required and no dependency on a container restart.
    """
    summary = {}
    for stage in GameStage.query.filter_by(state="pending").all():
        mapped = _reconcile_stage(stage)
        summary[stage.slug] = mapped
        _audit(stage, "reconcile", {"mapped": mapped, "expected": stage.expected_challenge_count})
    db.session.commit()
    return summary


@wargame_stages_bp.route("/admin/<slug>/sync", methods=["POST"])
@admins_only
def sync(slug):
    stage = GameStage.query.filter_by(slug=slug).first_or_404()
    if stage.state != "pending":
        abort(409, description="challenge mappings cannot change after a game starts")
    mapped = _reconcile_stage(stage)
    _audit(stage, "sync", {"mapped": mapped, "expected": stage.expected_challenge_count})
    db.session.commit()
    flash(f"{stage.name}: mapped {mapped} of {stage.expected_challenge_count} expected challenges.", "info")
    return redirect(url_for("wargame_stages.admin"))


@wargame_stages_bp.route("/machine/reconcile", methods=["POST"])
@bypass_csrf_protection
def machine_reconcile():
    """Non-interactive counterpart to /admin/<slug>/sync, for
    CEI-Labs-Wargames/deploy.sh to call right after pushing challenge
    content — same X-Sync-Auth / plugin_shared_secret pattern as
    instance-launcher's /admin/mappings/sync, so newly-deployed challenges
    are guaranteed hidden without an admin having to remember to visit this
    plugin's admin page first. See docs/staggered-wargame-stages.md."""
    provided = request.headers.get("X-Sync-Auth", "")
    expected = read_secret("plugin_shared_secret")
    if not expected or not hmac.compare_digest(provided, expected):
        abort(401)
    return jsonify(reconcile_all_pending())


@wargame_stages_bp.route("/admin/<slug>/start", methods=["POST"])
@admins_only
def start(slug):
    stage = GameStage.query.filter_by(slug=slug).with_for_update().first_or_404()
    count = _mapped_count(stage)
    if count != stage.expected_challenge_count:
        abort(409, description=f"expected {stage.expected_challenge_count} mapped challenges; found {count}")
    now = datetime.utcnow()
    try:
        stage.state, stage.started_at, changed = start_stage(stage.state, stage.started_at, now)
    except InvalidTransition as exc:
        abort(409, description=str(exc))
    if changed:
        stage.started_by = _admin_id()
        stage.scoreboard_visible = True
        ids = [row.challenge_id for row in GameStageChallenge.query.filter_by(stage_id=stage.id).all()]
        Challenges.query.filter(Challenges.id.in_(ids)).update({Challenges.state: "visible"}, synchronize_session=False)
        _audit(stage, "start", {"started_at": now.isoformat()})
        db.session.commit()
    return redirect(url_for("wargame_stages.admin"))


@wargame_stages_bp.route("/admin/<slug>/lock", methods=["POST"])
@admins_only
def lock(slug):
    stage = GameStage.query.filter_by(slug=slug).with_for_update().first_or_404()
    now = datetime.utcnow()
    try:
        stage.state, stage.locked_at, changed = lock_stage(stage.state, stage.locked_at, now)
    except InvalidTransition as exc:
        abort(409, description=str(exc))
    if changed:
        stage.locked_by = _admin_id()
        _audit(stage, "lock", {"locked_at": now.isoformat()})
        db.session.commit()
    return redirect(url_for("wargame_stages.admin"))


@wargame_stages_bp.route("/admin/<slug>/close", methods=["POST"])
@admins_only
def close(slug):
    stage = GameStage.query.filter_by(slug=slug).with_for_update().first_or_404()
    now = datetime.utcnow()
    try:
        stage.state, stage.locked_at, stage.closed_at, changed = close_stage(stage.state, stage.locked_at, stage.closed_at, now)
    except InvalidTransition as exc:
        abort(409, description=str(exc))
    if changed:
        stage.closed_by = _admin_id()
        _audit(stage, "close", {"closed_at": now.isoformat(), "score_cutoff": stage.locked_at.isoformat()})
        db.session.commit()
    return redirect(url_for("wargame_stages.admin"))


@wargame_stages_bp.route("/admin/<slug>/visibility", methods=["POST"])
@admins_only
def visibility(slug):
    stage = GameStage.query.filter_by(slug=slug).with_for_update().first_or_404()
    stage.scoreboard_visible = request.form.get("visible") == "true"
    _audit(stage, "show_scoreboard" if stage.scoreboard_visible else "hide_scoreboard")
    db.session.commit()
    return redirect(url_for("wargame_stages.admin"))
