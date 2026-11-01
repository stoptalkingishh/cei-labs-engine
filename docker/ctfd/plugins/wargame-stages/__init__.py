"""Per-game start controls and scoreboards for the staged wargame event."""
from CTFd.models import db
from CTFd.plugins import (
    register_admin_plugin_menu_bar,
    register_plugin_assets_directory,
    register_plugin_stylesheet,
    register_user_page_menu_bar,
)

from .models import GameStage, GameStageAudit, GameStageChallenge
from .routes import reconcile_all_pending, wargame_stages_bp


DEFAULT_STAGES = (
    ("bandit", "Bandit", "Linux Basics", 1, 35),
    ("krypton", "Krypton", "Cryptography", 2, 8),
    ("natas", "Natas", "Web Security", 3, 16),
    ("sentinel", "Sentinel - Security Operations", "Security Operations", 4, 22),
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
    # Named "Scoreboard" (not "Game scoreboards") deliberately: this event's
    # win condition is per-track individual ranking, not CTFd core's native
    # team-aggregate scoreboard (hidden via nav.css below), so this IS the
    # scoreboard that matters -- there should only ever be one nav entry
    # with that label, not two competing ones.
    register_user_page_menu_bar("Scoreboard", "/plugins/wargame-stages/")
    register_plugin_assets_directory(app, base_path="/plugins/wargame-stages/assets/")
    register_plugin_stylesheet("/plugins/wargame-stages/assets/nav.css")
