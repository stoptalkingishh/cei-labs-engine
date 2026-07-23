"""docker/ctfd/plugins/modal-theme

Subtle visual polish for CTFd's challenge-detail modal (the popup shown
when a participant clicks into a challenge to read its description, view
hints, and submit a flag). Deliberately CSS-only and deliberately scoped
to just that modal -- see assets/challenge-modal.css for the full design
rationale.

Registered via register_plugin_assets_directory/register_plugin_stylesheet,
the same mechanism instance-launcher/__init__.py already uses for its JS
(register_plugin_assets_directory/register_plugin_script) -- CTFd core
(CTFd/plugins/__init__.py) exposes a stylesheet counterpart to that script
helper for exactly this purpose, so this reuses the existing, working
pattern rather than inventing a new asset-injection path.

No JS, no new routes, no new tables: this plugin only ships one CSS file.
The stylesheet is scoped entirely under #challenge-window (the modal's own
container, per CTFd core's themes/core/templates/challenges.html) so it
cannot leak onto the scoreboard, login page, or challenge board -- and it
styles only Bootstrap classes CTFd's own challenge.html template already
renders (.modal-content, .nav-tabs, .challenge-name, .challenge-tag,
.challenge-hints, #instance-launcher-panel, etc.), so the instance-launcher
"Launch Environment" panel and the native hint-unlock UI inherit the same
calm styling for free instead of needing per-plugin overrides.

CTFd's plugin loader imports this package and calls load(app) once at
startup (CTFd/utils/initialization, matches every other CTFd plugin, e.g.
instance-launcher/__init__.py, hint-wallet/__init__.py).
"""
from CTFd.plugins import register_plugin_assets_directory, register_plugin_stylesheet


def load(app):
    register_plugin_assets_directory(app, base_path="/plugins/modal-theme/assets/")
    register_plugin_stylesheet("/plugins/modal-theme/assets/challenge-modal.css")
