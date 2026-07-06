"""docker/ctfd/plugins/instance-launcher

Adds a self-service "Launch Environment" flow for challenges configured with
an instance type (Juice Shop today; target+attacker wargames once that
content ships) — see docker/orchestrator/README.md for the instance types
and access model this drives.

Participants reach the launch page via a link that lives in the challenge's
own description (maintained by scripts/challenges-load.sh's sync step), so
it works inside the challenge view regardless of CTFd theme/frontend version
— see routes.py's module docstring for why this plugin doesn't attempt to
inject a button directly into the challenge modal's DOM.

CTFd's plugin loader imports this package and calls load(app) once at
startup (CTFd/utils/initialization, matches every other CTFd plugin, e.g.
CTFd/plugins/dynamic_challenges).
"""
from CTFd.models import db

from .models import InstanceChallengeConfig  # noqa: F401 (import registers the model with SQLAlchemy)
from .routes import instance_launcher_bp


def load(app):
    app.register_blueprint(instance_launcher_bp)
    # InstanceChallengeConfig is a brand-new table with no prior schema to
    # migrate from, so create-if-missing is sufficient — unlike a real Alembic
    # migration (needed when altering an existing CTFd table), there's no
    # history to reconcile here.
    with app.app_context():
        InstanceChallengeConfig.__table__.create(bind=db.engine, checkfirst=True)
