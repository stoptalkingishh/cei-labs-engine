"""docker/ctfd/plugins/hint-wallet

Bridges the CEI-Labs-Wargames release pipeline's signed hint-wallet catalog
sync (deploy.sh's sync_hint_wallet_bundle(), POSTing to
${CTFD_URL}/plugins/hint-wallet/machine/sync) to the orchestrator's
Docker-overlay-internal /wallet/sync, /wallet/unlock, and
/wallet/unlocked/<owner_id>/<track>/<entry_name> endpoints
(docker/orchestrator/app/main.py, wallet.py) -- see
docs/P0-FIX-LOG-2026-07-23.md for the original gap this closed, and
cei-labs-event#7 for the percent-of-value cost model, progression-window
gating, and per-challenge solve-time score reduction added since (no shared
team-currency wallet exists anywhere in this system anymore). Modeled
structurally on instance-launcher/__init__.py: one blueprint registered
here, one brand-new table created if missing, one script injected into the
challenge modal the same way instance-launcher/__init__.py registers
challenge-launch.js, and (per cei-labs-event#7) a solve_hook registered the
same way instance-launcher/solve_hook.py already is, so a correct flag
submission can reduce that challenge's own award if the solving owner had
opened a hint tier for it. See assets/hint-wallet.js's header comment for
the frontend injection pattern and the category->track mapping it needs
that doesn't exist anywhere else CTFd-side.

CTFd's plugin loader imports this package and calls load(app) once at
startup (CTFd/utils/initialization, matches every other CTFd plugin).
"""
from CTFd.models import db
from CTFd.plugins import register_plugin_assets_directory, register_plugin_script

from . import solve_hook
from .models import HintWalletCatalog  # noqa: F401 (import registers the model with SQLAlchemy)
from .routes import hint_wallet_bp


def load(app):
    app.register_blueprint(hint_wallet_bp)
    # Brand-new table, no prior schema to migrate from -- create-if-missing
    # is sufficient, same reasoning as instance-launcher/__init__.py.
    with app.app_context():
        HintWalletCatalog.__table__.create(bind=db.engine, checkfirst=True)

    register_plugin_assets_directory(app, base_path="/plugins/hint-wallet/assets/")
    register_plugin_script("/plugins/hint-wallet/assets/hint-wallet.js")
    solve_hook.register(app)
