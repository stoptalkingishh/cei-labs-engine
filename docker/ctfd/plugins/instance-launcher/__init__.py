"""docker/ctfd/plugins/instance-launcher

Adds a self-service "Launch Environment" flow for challenges configured with
an instance type (Juice Shop today; target+attacker wargames once that
content ships) — see docker/orchestrator/README.md for the instance types
and access model this drives.

Participants reach the launch controls through a control injected directly
into CTFd's own challenge modal by assets/challenge-launch.js (registered
below via register_plugin_script/register_plugin_assets_directory) — not
through a link in the challenge description, which was this plugin's
original design (see git history) before live playtesting showed a plain
link was too easy to miss entirely. challenge-launch.js talks to the JSON
routes in routes.py (/api/status/<id>, /api/launch/<id>); the original
full-page HTML flow (/launch/<id>) is kept working unmodified as a fallback
in case the injected JS ever fails to load.

CTFd's plugin loader imports this package and calls load(app) once at
startup (CTFd/utils/initialization, matches every other CTFd plugin, e.g.
CTFd/plugins/dynamic_challenges).
"""
from CTFd.models import db
from CTFd.plugins import register_plugin_assets_directory, register_plugin_script

from . import flags, solve_hook
from .models import InstanceChallengeConfig, TeamChallengeSecret  # noqa: F401 (import registers the models with SQLAlchemy)
from .routes import instance_launcher_bp


def load(app):
    app.register_blueprint(instance_launcher_bp)
    # Both are brand-new tables with no prior schema to migrate from, so
    # create-if-missing is sufficient — unlike a real Alembic migration
    # (needed when altering an existing CTFd table), there's no history to
    # reconcile here. checkfirst=True makes this a no-op once either table
    # already exists.
    with app.app_context():
        InstanceChallengeConfig.__table__.create(bind=db.engine, checkfirst=True)
        TeamChallengeSecret.__table__.create(bind=db.engine, checkfirst=True)
    solve_hook.register(app)
    flags.register(app)

    register_plugin_assets_directory(app, base_path="/plugins/instance-launcher/assets/")
    register_plugin_script("/plugins/instance-launcher/assets/challenge-launch.js")
