import json
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_user, is_admin

from .logic import InvalidTransition, SolveEvent, close_stage, lock_stage, rank_solves, start_stage
from .models import GameStage, GameStageAudit, GameStageChallenge

wargame_stages_bp = Blueprint("wargame_stages", __name__, template_folder="templates", url_prefix="/plugins/wargame-stages")


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


@wargame_stages_bp.route("/admin/<slug>/sync", methods=["POST"])
@admins_only
def sync(slug):
    stage = GameStage.query.filter_by(slug=slug).first_or_404()
    if stage.state != "pending":
        abort(409, description="challenge mappings cannot change after a game starts")
    challenges = Challenges.query.filter_by(category=stage.category).order_by(Challenges.id).all()
    ids = {challenge.id for challenge in challenges}
    conflicts = GameStageChallenge.query.filter(GameStageChallenge.challenge_id.in_(ids), GameStageChallenge.stage_id != stage.id).count() if ids else 0
    if conflicts:
        abort(409, description="one or more challenges already belong to another game stage")
    GameStageChallenge.query.filter_by(stage_id=stage.id).filter(~GameStageChallenge.challenge_id.in_(ids)).delete(synchronize_session=False)
    existing = {row.challenge_id for row in GameStageChallenge.query.filter_by(stage_id=stage.id).all()}
    for challenge in challenges:
        if challenge.id not in existing:
            db.session.add(GameStageChallenge(stage_id=stage.id, challenge_id=challenge.id))
        if stage.state == "pending":
            challenge.state = "hidden"
    _audit(stage, "sync", {"mapped": len(ids), "expected": stage.expected_challenge_count})
    db.session.commit()
    flash(f"{stage.name}: mapped {len(ids)} of {stage.expected_challenge_count} expected challenges.", "info")
    return redirect(url_for("wargame_stages.admin"))


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
