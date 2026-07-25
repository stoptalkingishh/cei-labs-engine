"""docker/ctfd/plugins/submission-history

Closes the gap tracked in cei-labs-event#6: CTFd's own `Submissions.provided`
field (inherited by `Solves`) already stores the exact flag text a player
submitted for every attempt, correct or not (CTFd/models/__init__.py), but
CTFd's stock `/api/v1/submissions` API is entirely `@admins_only`
(CTFd/api/v1/submissions.py) -- there is no player-facing way to read a flag
back after solving. This matters concretely for this event's own tracks:
Bandit/Krypton/Natas progression reuses the previous level's password to log
back in (SSH) or authenticate (Natas), so a player who reconnects later
(new SSH session, browser restart) and has forgotten a password they already
earned previously had no way to look it back up short of re-solving.

No new database table -- this only *reads* CTFd's own existing Solves rows,
scoped strictly to the requesting account. Modeled structurally on
modal-theme/wargame-stages/instance-launcher's own __init__.py: one
blueprint registered here, nothing else to create at startup.

CTFd's plugin loader imports this package and calls load(app) once at
startup (CTFd/utils/initialization, matches every other CTFd plugin in this
repo, e.g. hint-wallet/__init__.py, instance-launcher/__init__.py).
"""
from CTFd.plugins import register_plugin_assets_directory, register_plugin_script

from .routes import submission_history_bp


def load(app):
    app.register_blueprint(submission_history_bp)

    register_plugin_assets_directory(app, base_path="/plugins/submission-history/assets/")
    register_plugin_script("/plugins/submission-history/assets/submission-history.js")
