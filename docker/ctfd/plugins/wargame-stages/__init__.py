"""Per-game start controls and scoreboards for the staged wargame event."""
from CTFd.models import db
from CTFd.plugins import register_admin_plugin_menu_bar, register_user_page_menu_bar

from .models import GameStage, GameStageAudit, GameStageChallenge
from .routes import reconcile_all_pending, wargame_stages_bp


DEFAULT_STAGES = (
    ("bandit", "Bandit", "Linux Basics", 1, 35),
    ("krypton", "Krypton", "Cryptography", 2, 8),
    ("natas", "Natas", "Web Security", 3, 16),
)


def load(app):
    app.register_blueprint(wargame_stages_bp)
    with app.app_context():
        for table in (GameStage.__table__, GameStageChallenge.__table__, GameStageAudit.__table__):
            table.create(bind=db.engine, checkfirst=True)
        for slug, name, category, order, count in DEFAULT_STAGES:
            if GameStage.query.filter_by(slug=slug).first() is None:
                db.session.add(GameStage(
                    slug=slug, name=name, category=category,
                    display_order=order, expected_challenge_count=count,
                ))
        db.session.commit()
        # Hide-by-default enforcement on every app start (container restart,
        # fresh redeploy). Covers content that landed via ctfcli/deploy.sh
        # without going through /machine/reconcile (e.g. a manual API push,
        # or a deploy that ran before this plugin's endpoint existed).
        reconcile_all_pending()

    register_admin_plugin_menu_bar("Wargame stages", "/plugins/wargame-stages/admin")
    register_user_page_menu_bar("Game scoreboards", "/plugins/wargame-stages/")
