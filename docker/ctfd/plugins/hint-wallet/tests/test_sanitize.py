"""Unit tests for hint-wallet/sanitize.py -- the sanitize half of the
markdown-then-sanitize pair that stands between authored hint content and
hint-wallet.js's `contentDiv.innerHTML = body.content`.

routes.py's own tests (test_routes.py::test_api_unlock_sanitizes_hint_content
_after_markdown) prove routes.py calls this; these prove the sanitizer
actually removes what it claims to, including the delegation rule that decides
whether CTFd's own nh3-backed sanitizer or the local allowlist runs.
"""
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_sanitize():
    # Loaded under a private name rather than as `hint_wallet.sanitize` so this
    # module doesn't depend on (or collide with) the CTFd stubbing test_routes.py
    # does at import time -- sanitize.py has no hard CTFd import, so it loads
    # standalone.
    spec = importlib.util.spec_from_file_location(
        "hint_wallet_sanitize_under_test", Path(__file__).resolve().parents[1] / "sanitize.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sanitize_mod = _load_sanitize()


# ── allowlist behavior (the local fallback implementation) ─────────────────

@pytest.mark.parametrize(
    "payload",
    [
        "<script>fetch('/api/v1/flags')</script>",
        "<SCRIPT>alert(1)</SCRIPT>",
        "<img src=x onerror=alert(1)>",
        "<a href=\"javascript:alert(1)\">click</a>",
        "<a href=\"java\tscript:alert(1)\">click</a>",  # tab-obfuscated scheme
        "<a href=\"jav&#x09;ascript:alert(1)\">click</a>",  # entity-obfuscated scheme
        "<a href=\"data:text/html,<script>alert(1)</script>\">click</a>",
        "<iframe src=\"https://evil.example/x\"></iframe>",
        "<svg onload=alert(1)></svg>",
        "<style>body{display:none}</style>",
        "<form action=\"https://evil.example\"><input name=\"password\"></form>",
        "<!-- [if IE]><script>alert(1)</script><![endif] -->",
    ],
)
def test_dangerous_markup_is_neutralized(payload):
    cleaned = sanitize_mod.sanitize_html(payload)
    lowered = cleaned.lower()
    for dangerous in (
        "<script", "onerror", "onload", "javascript:", "<iframe", "<svg",
        "<style", "<form", "<input", "data:text/html",
    ):
        assert dangerous not in lowered, f"{dangerous!r} survived: {payload!r} -> {cleaned!r}"


def test_script_content_is_dropped_not_just_its_tags():
    """<script>/<style> are raw-text elements: the browser hands everything up
    to the matching end tag back to the parser, so unwrapping them (keeping
    the text) would re-expose the payload rather than remove it."""
    assert sanitize_mod.sanitize_html("<script>alert(1)</script>") == ""


@pytest.mark.parametrize(
    "payload,expected_fragment",
    [
        ("<p>plain</p>", "<p>plain</p>"),
        ("<p>try <code>ssh -h</code></p>", "<code>ssh -h</code>"),
        ("<p><strong>bold</strong> and <em>italic</em></p>", "<strong>bold</strong>"),
        ("<a href=\"https://example.com/ref\">ref</a>", '<a href="https://example.com/ref">ref</a>'),
        ("<a href=\"/relative/path\">rel</a>", '<a href="/relative/path">rel</a>'),
        ("<a href=\"mailto:ctf@example.com\">mail</a>", 'href="mailto:ctf@example.com"'),
        ("<img src=\"https://example.com/a.png\" alt=\"a\">", '<img src="https://example.com/a.png" alt="a">'),
        ("<ul><li>one</li><li>two</li></ul>", "<li>one</li>"),
        ("<pre><code class=\"language-bash\">ls</code></pre>", 'class="language-bash"'),
    ],
)
def test_legitimate_rendered_markdown_survives(payload, expected_fragment):
    """A sanitizer that ate everything would be a "fix" too: the Markdown
    rendering in routes.py exists so hints actually render."""
    assert expected_fragment in sanitize_mod.sanitize_html(payload)


def test_text_content_is_escaped_not_reinterpreted():
    cleaned = sanitize_mod.sanitize_html("<p>5 &lt; 6 &amp; 7</p>")
    assert cleaned == "<p>5 &lt; 6 &amp; 7</p>"


def test_disallowed_wrapper_is_unwrapped_keeping_its_text():
    # Unlike script/style, an unknown-but-harmless wrapper contributes nothing
    # once its tag is gone, so its text is kept -- otherwise a hint written
    # with, say, a <marquee> would silently lose its content.
    assert sanitize_mod.sanitize_html("<marquee>hint text</marquee>") == "hint text"


# ── delegation to CTFd's own sanitizer ─────────────────────────────────────

def test_uses_ctfds_own_sanitizer_when_importable(monkeypatch):
    """In a real CTFd deployment the nh3-backed CTFd sanitizer is the one
    available, and it should be used so hint content and challenge content
    share a single policy rather than a lookalike."""
    calls = []

    def fake_ctfd_sanitizer(html):
        calls.append(html)
        return "<p>ctfd-policy</p>"

    monkeypatch.setattr(sanitize_mod, "_ctfd_sanitize_html", fake_ctfd_sanitizer)
    assert sanitize_mod.sanitize_html("<script>x</script>") == "<p>ctfd-policy</p>"
    assert calls == ["<script>x</script>"]


def test_falls_back_to_local_allowlist_when_ctfd_sanitizer_is_unavailable(monkeypatch):
    """The fallback still has to be a real boundary, not a passthrough -- a
    silent no-op here would reintroduce the exact XSS this module exists to
    close (and would be invisible in this repo's test suites, which stub
    CTFd.* and so take this path)."""
    monkeypatch.setattr(sanitize_mod, "_ctfd_sanitize_html", None)
    cleaned = sanitize_mod.sanitize_html('<a href="javascript:alert(1)">c</a><script>y</script>')
    assert "javascript:" not in cleaned
    assert "<script" not in cleaned


def test_empty_input_is_returned_unchanged(monkeypatch):
    monkeypatch.setattr(sanitize_mod, "_ctfd_sanitize_html", None)
    assert sanitize_mod.sanitize_html("") == ""
