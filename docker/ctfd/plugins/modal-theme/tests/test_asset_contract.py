"""Asset-loading contract tests for docker/ctfd/plugins/modal-theme.

modal-theme is CSS-only (no CTFd/Flask/SQLAlchemy imports at module
level), so unlike instance-launcher's tests this doesn't need to stub out
CTFd internals -- it can just read __init__.py and the CSS file directly
off disk and check the same two things that matter for a plugin whose
entire job is "inject a stylesheet into someone else's page":

1. The stylesheet CTFd is told to load (__init__.py's
   register_plugin_stylesheet call) actually exists on disk at that path,
   and is genuinely CSS -- no <script>, no javascript: URIs, no CSS
   expression()/behavior: tricks -- so a plugin that's supposed to be a
   passive style layer can't smuggle in script execution.
2. Every rule in the stylesheet is scoped under #challenge-window (the
   challenge-detail modal's own container -- see the CSS file's header
   comment for how that was confirmed against CTFd core's own template),
   so this plugin can never leak styling onto the scoreboard, challenge
   board, or login page, no matter what gets added to it later.
"""
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
INIT_PY = PLUGIN_DIR / "__init__.py"
CSS_PATH = PLUGIN_DIR / "assets" / "challenge-modal.css"

FORBIDDEN_PATTERNS = [
    re.compile(r"<script", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"expression\s*\(", re.IGNORECASE),
    re.compile(r"behavior\s*:", re.IGNORECASE),
    re.compile(r"@import", re.IGNORECASE),  # no fetching of remote stylesheets
]


def _registered_stylesheet_path():
    """Pulls the path passed to register_plugin_stylesheet out of
    __init__.py without importing it (importing requires CTFd)."""
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'register_plugin_stylesheet\(\s*"([^"]+)"', text)
    assert match, "__init__.py must call register_plugin_stylesheet(...) with a literal path"
    return match.group(1)


def _strip_comments(css_text):
    return re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)


def _top_level_selectors(css_text):
    """Returns the selector text preceding every `{` in the stylesheet."""
    stripped = _strip_comments(css_text)
    return [sel.strip() for sel in re.findall(r"([^{}]+)\{", stripped)]


def test_registered_stylesheet_exists_on_disk():
    registered = _registered_stylesheet_path()
    assert registered.endswith("assets/challenge-modal.css"), registered
    assert CSS_PATH.is_file(), f"registered stylesheet {registered} not found at {CSS_PATH}"


def test_assets_directory_matches_registration():
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'register_plugin_assets_directory\(\s*app,\s*base_path="([^"]+)"', text)
    assert match, "__init__.py must register an assets directory matching the stylesheet's URL prefix"
    base_path = match.group(1)
    registered = _registered_stylesheet_path()
    assert registered.startswith(base_path), (registered, base_path)


def test_css_contains_no_script_or_expression_injection():
    css_text = CSS_PATH.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_PATTERNS:
        assert not pattern.search(css_text), f"forbidden pattern {pattern.pattern!r} found in {CSS_PATH}"


def test_css_braces_are_balanced():
    css_text = _strip_comments(CSS_PATH.read_text(encoding="utf-8"))
    assert css_text.count("{") == css_text.count("}")
    assert css_text.count("{") > 0, "expected at least one CSS rule"


def test_every_rule_is_scoped_to_challenge_window():
    css_text = CSS_PATH.read_text(encoding="utf-8")
    selectors = _top_level_selectors(css_text)
    assert selectors, "expected at least one CSS rule"
    for selector in selectors:
        # Each comma-separated selector in a rule must itself be scoped;
        # a bare `, body { ... }` tacked onto an otherwise-scoped rule
        # would still leak.
        for part in selector.split(","):
            part = part.strip()
            assert part.startswith("#challenge-window"), (
                f"selector {part!r} is not scoped under #challenge-window "
                "-- this plugin must only style the challenge-detail modal"
            )


def test_no_bright_or_animated_declarations():
    """Guards the 'not too distracting' design intent structurally: no
    animation/transition/keyframes and no pure-saturated primary colors
    (red/green/yellow/etc as bare CSS color keywords) should ever sneak
    into this file. Muted hex/rgba values are unaffected."""
    css_text = _strip_comments(CSS_PATH.read_text(encoding="utf-8")).lower()
    for banned in ("@keyframes", "animation:", "animation-", "transition:"):
        assert banned not in css_text, f"unexpected {banned!r} in a 'not too distracting' modal stylesheet"
    for keyword in ("red", "lime", "yellow", "magenta", "fuchsia", "orange"):
        assert re.search(rf":\s*{keyword}\b", css_text) is None, (
            f"bare CSS color keyword {keyword!r} found -- keep this modal's palette muted"
        )
