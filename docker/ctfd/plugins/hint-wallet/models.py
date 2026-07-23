"""docker/ctfd/plugins/hint-wallet/models.py

A single cached copy of the most recently *accepted* hint-wallet catalog
bundle (the orchestrator is the source of truth and the only place balances
and unlocks are recorded -- see wallet.py's WalletStore) -- kept here purely
so /api/tiers/<track>/<entry_name> can list a hint's tier numbers and costs
to a player WITHOUT exposing tier content and without needing a browse-only
endpoint on the orchestrator (it doesn't have one: /wallet/deduct both
reads and spends in the same call, by design, so it can't be used for a
free preview). machine_sync() in routes.py writes this row only after the
orchestrator's own /wallet/sync has accepted the bundle (status 200) --
never before, so this cache can't get ahead of what the orchestrator would
actually honor a deduct against.

A brand-new table, created the same create-if-missing way __init__.py
already uses for every other plugin table in this repo (see
instance-launcher/models.py's InstanceChallengeConfig/TeamChallengeSecret).
"""
from datetime import datetime

from CTFd.models import db


class HintWalletCatalog(db.Model):
    __tablename__ = "hint_wallet_catalog_cache"

    # Singleton row (id always 1) -- one bundle covers all three tracks at
    # once (wallet.py's REQUIRED_TRACKS), matching the orchestrator's own
    # wallet_catalog table shape (store.py).
    id = db.Column(db.Integer, primary_key=True)
    revision = db.Column(db.Integer, nullable=True)
    bundle_json = db.Column(db.Text, nullable=False)
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
