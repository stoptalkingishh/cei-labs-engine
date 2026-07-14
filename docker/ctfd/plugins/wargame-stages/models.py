from datetime import datetime

from CTFd.models import db


class GameStage(db.Model):
    __tablename__ = "wargame_stages"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(32), nullable=False, unique=True)
    name = db.Column(db.String(64), nullable=False)
    category = db.Column(db.String(128), nullable=False, unique=True)
    display_order = db.Column(db.Integer, nullable=False)
    expected_challenge_count = db.Column(db.Integer, nullable=False)
    state = db.Column(db.String(16), nullable=False, default="pending")
    scoreboard_visible = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime)
    locked_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    started_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    locked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    closed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class GameStageChallenge(db.Model):
    __tablename__ = "wargame_stage_challenges"
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey("wargame_stages.id", ondelete="CASCADE"), nullable=False, index=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, unique=True)
    __table_args__ = (db.UniqueConstraint("stage_id", "challenge_id", name="uq_wargame_stage_challenge"),)


class GameStageAudit(db.Model):
    __tablename__ = "wargame_stage_audit"
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey("wargame_stages.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    action = db.Column(db.String(32), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
