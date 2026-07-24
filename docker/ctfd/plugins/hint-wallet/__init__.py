"""docker/ctfd/plugins/hint-wallet

Bridges the CEI-Labs-Wargames release pipeline's signed hint-wallet catalog
sync (deploy.sh's sync_hint_wallet_bundle(), POSTing to
${CTFD_URL}/plugins/hint-wallet/machine/sync) to the orchestrator's
Docker-overlay-internal /wallet/sync, /wallet/deduct, and
/wallet/balance/<owner_id> endpoints (docker/orchestrator/app/main.py,
wallet.py) -- see docs/P0-FIX-LOG-2026-07-23.md for the full gap this
closes. Modeled structurally on instance-launcher/__init__.py: one
blueprint registered here, one brand-new table created if missing, and
(as of this frontend pass) one script injected into the challenge modal the
same way instance-launcher/__init__.py registers challenge-launch.js --
before this, the backend had no participant-facing UI at all: players could
not see or unlock a single hint. See assets/hint-wallet.js's header comment
for the injection pattern and the category->track mapping it needs that
doesn't exist anywhere else CTFd-side.

CTFd's plugin loader imports this package and calls load(app) once at
startup (CTFd/utils/initialization, matches every other CTFd plugin).
"""
from CTFd.models import db
from CTFd.plugins import register_plugin_assets_directory, register_plugin_script

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
