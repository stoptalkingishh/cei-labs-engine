"""Register CTFGenerator-backed typed answer flags with CTFd."""

from CTFd.plugins import register_plugin_assets_directory

from . import flags


def load(app):
    flags.register(app)
    register_plugin_assets_directory(
        app, base_path="/plugins/typed-answer-flags/assets/"
    )
