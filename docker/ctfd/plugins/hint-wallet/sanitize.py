"""docker/ctfd/plugins/hint-wallet/sanitize.py

The HTML allowlist stage for authored hint text.

routes.py renders hint `content` (a cmark-gfm Markdown source string coming
back from the orchestrator) to HTML for hint-wallet.js to drop into
`contentDiv.innerHTML`. cmark-gfm is a Markdown RENDERER, not a sanitizer:
CTFd's own `markdown()` runs with Options.CMARK_OPT_UNSAFE, so raw HTML in the
source passes straight through into the rendered output. Anything that can get
a foothold into the hint content pipeline (a content-push job, a wargames
maintainer account, a compromised release) would therefore land as stored XSS
in every player's browser inside the CTFd origin, holding their session --
enough to submit flags on their behalf and drive the launcher API.

CTFd's own boundary for this is a SEPARATE sanitize step layered on top of
markdown (CTFd.utils.config.pages.build_markdown() -> sanitize_html(), i.e. the
`{{ ... |markdown|sanitize }}` shape its challenge templates use). This module
is that step for this plugin.

Two implementations, one entry point:

  - `CTFd.utils.security.sanitize.sanitize_html` when it is importable. In a
    real CTFd deployment (the CTFd package is fully loaded before plugins are)
    this always is, and it is the nh3-backed allowlist CTFd itself uses, so
    hint content and challenge content are sanitized by literally the same
    policy rather than a lookalike.
  - A small stdlib allowlist sanitizer (html.parser-based) as the fallback, for
    environments where that import isn't available -- including this repo's
    per-plugin test suites, which stub `CTFd.*` (see tests/test_routes.py) and
    therefore can't import anything under the real CTFd package. It is
    deliberately strict: anything not on ALLOWED_TAGS is unwrapped (tag
    dropped, text kept) or, for raw-text elements like <script>, dropped
    whole; only a fixed set of attributes survives; and URL-bearing attributes
    are scheme-checked, so `javascript:` and `data:` hrefs never survive.
"""
import re
from html import escape
from html.parser import HTMLParser

try:  # pragma: no cover - the real CTFd package is present in production
    from CTFd.utils.security.sanitize import sanitize_html as _ctfd_sanitize_html
except ImportError:
    _ctfd_sanitize_html = None


# Everything cmark-gfm emits for the Markdown subset hint text is authored in
# (paragraphs, code fences, lists, tables, emphasis, links, images), so this
# list loses nothing legitimate while dropping script/iframe/object/embed/svg
# and friends outright.
ALLOWED_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd",
    "li", "ol", "p", "pre", "s", "samp", "small", "span", "strong", "sub", "sup",
    "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
})

# Raw-text/RCDATA elements: the browser parses everything up to the matching
# end tag as text, so unwrapping these (keeping their content) would hand the
# content straight back to the parser. Drop the element AND its content.
_DROP_CONTENT_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "template", "noscript",
    "svg", "math", "xmp", "plaintext",
})

VOID_TAGS = frozenset({"br", "hr", "img"})

# Per-tag attribute allowlist. `class` only on the elements cmark-gfm gives a
# language class to (fenced code blocks) plus a couple of generic wrappers.
ALLOWED_ATTRIBUTES = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "code": frozenset({"class"}),
    "pre": frozenset({"class"}),
    "span": frozenset({"class"}),
    "div": frozenset({"class"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

# Attributes whose value is a URL: `href="javascript:..."` executes on click,
# which is why a tag allowlist alone isn't a sanitizer.
URL_ATTRIBUTES = frozenset({"href", "src"})

SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

# Characters browsers ignore while resolving a URL scheme. Stripped before the
# scheme check so `java\tscript:alert(1)` can't smuggle one past it.
_URL_NOISE = re.compile(r"[\x00-\x20\x7f]")


def _is_safe_url(value: str) -> bool:
    """True for relative URLs and URLs on an allowlisted scheme.

    Anything with a scheme that isn't in SAFE_URL_SCHEMES -- javascript:,
    data:, vbscript: -- is rejected, which is what keeps a Markdown link like
    `[click](javascript:alert(1))` from becoming a click-to-execute XSS once
    the rendered HTML reaches innerHTML.
    """
    if value is None:
        return False
    candidate = _URL_NOISE.sub("", value)
    head, sep, _rest = candidate.partition(":")
    if not sep:
        return True  # relative URL: no scheme to abuse
    # A colon that appears after a path/query/fragment delimiter isn't a
    # scheme separator (`/a:b` is a relative path), so check position first.
    if any(delimiter in head for delimiter in "/?#"):
        return True
    return head.lower() in SAFE_URL_SCHEMES


class _AllowlistSanitizer(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True collapses entities into text data, which is
        # then re-escaped on output -- so an encoded payload can't survive as
        # a live entity in the serialized result.
        super().__init__(convert_charrefs=True)
        self._out: list = []
        self._drop_tag: "str | None" = None
        self._drop_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._drop_tag is not None:
            if tag == self._drop_tag:
                self._drop_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self._drop_tag = tag
            self._drop_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return  # unwrap: text inside survives, the element does not
        self._out.append(self._start_markup(tag, attrs, self_closing=False))

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._drop_tag is not None or tag in _DROP_CONTENT_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            return
        self._out.append(self._start_markup(tag, attrs, self_closing=True))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._drop_tag is not None:
            if tag == self._drop_tag:
                self._drop_depth -= 1
                if self._drop_depth <= 0:
                    self._drop_tag = None
            return
        if tag in VOID_TAGS or tag not in ALLOWED_TAGS:
            return
        self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._drop_tag is not None:
            return
        self._out.append(escape(data, quote=False))

    def handle_entityref(self, name):  # only reached with convert_charrefs off
        self.handle_data(f"&{name};")

    def handle_charref(self, name):  # only reached with convert_charrefs off
        self.handle_data(f"&#{name};")

    def handle_comment(self, data):
        return  # comments are dropped: they can hide conditional-comment payloads

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    def _start_markup(self, tag, attrs, self_closing: bool) -> str:
        allowed = ALLOWED_ATTRIBUTES.get(tag, frozenset())
        rendered = []
        for name, value in attrs:
            name = (name or "").lower()
            if name not in allowed or value is None:
                continue
            if name in URL_ATTRIBUTES and not _is_safe_url(value):
                continue
            rendered.append(f' {name}="{escape(value, quote=True)}"')
        suffix = " /" if self_closing and tag not in VOID_TAGS else ""
        return f"<{tag}{''.join(rendered)}{suffix}>"

    def result(self) -> str:
        return "".join(self._out)


def _fallback_sanitize_html(html: str) -> str:
    parser = _AllowlistSanitizer()
    parser.feed(html)
    parser.close()
    return parser.result()


def sanitize_html(html: str) -> str:
    """Strip everything outside the allowlist from `html`.

    Delegates to CTFd's own sanitizer when it's importable so hint content and
    challenge content share one policy; falls back to the local allowlist
    implementation (see this module's header) otherwise.
    """
    if not html:
        return html
    if _ctfd_sanitize_html is not None:
        return _ctfd_sanitize_html(html)
    return _fallback_sanitize_html(html)
