"""Markdown written by ordinary users must not be able to run script.

``render_markdown`` renders markdown and marks it safe without sanitising, which is
sound only for admin-authored text. Python-Markdown passes raw HTML through, so the
same filter on rider-authored text (ticket details, application messages) is stored
XSS against whoever reads the page -- often an admin. ``render_markdown_untrusted``
renders the same markdown and then strips everything outside an allowlist.
"""

from html.parser import HTMLParser

import pytest

from apps.accounts.markdown_safe import render_untrusted_markdown
from apps.accounts.templatetags.accounts_tags import render_markdown, render_markdown_untrusted

XSS_PAYLOADS = [
    # Raw tags with event handlers
    'Hello <img src=x onerror="alert(1)">',
    "<script>alert(1)</script>",
    "<svg onload=alert(1)></svg>",
    "<svg><script>alert(1)</script></svg>",
    "<math><mtext><script>alert(1)</script></mtext></math>",
    '<div onclick="alert(1)">click me</div>',
    '<body onload="alert(1)">',
    '<details open ontoggle="alert(1)">x</details>',
    # Markdown links with dangerous schemes
    "[click](javascript:alert(1))",
    "[click](JaVaScRiPt:alert(1))",
    "[click](java\tscript:alert(1))",
    "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    # Raw anchors with dangerous schemes
    '<a href="javascript:alert(1)">raw</a>',
    '<a href=" javascript:alert(1)">leading space</a>',
    '<a href="&#106;avascript:alert(1)">entity encoded</a>',
    '<a href="vbscript:alert(1)">vb</a>',
    '<a href="data:text/html,<script>alert(1)</script>">data</a>',
    # Attribute breakouts
    '"><img src=x onerror=alert(1)>',
    '\'"><script>alert(1)</script>',
    '<a href="https://ok.test" onmouseover="alert(1)">hover</a>',
    '<a href="https://ok.test\\" onmouseover=\\"alert(1)">quote break</a>',
    # Active/embedding content
    '<iframe src="https://evil.test"></iframe>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<object data="https://evil.test"></object>',
    '<embed src="https://evil.test">',
    '<base href="https://evil.test/">',
    '<meta http-equiv="refresh" content="0;url=https://evil.test">',
    '<form action="https://evil.test"><button>go</button></form>',
    # Styling
    '<p style="position:fixed;top:0;left:0;width:100vw;height:100vh">overlay</p>',
    "<style>body{display:none}</style>",
    '<link rel="stylesheet" href="https://evil.test/x.css">',
    # Comment / obfuscation tricks
    "<!--<script>alert(1)</script>-->",
    "<scr<script>ipt>alert(1)</script>",
]

# Substrings that must never survive sanitising, whatever the payload. A live tag starts with
# "<name"; the same words kept as text are escaped to "&lt;name", which no browser runs.
FORBIDDEN = ("<script", "<iframe", "<object", "<embed", "<svg", "<style", "<base", "<meta", "<form", "<link")
FORBIDDEN_TAGS = frozenset(tag.lstrip("<") for tag in FORBIDDEN) | {"math"}
DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "data:")


class _Elements(HTMLParser):
    """Collect the elements a browser would build from the HTML: tags and attributes, not text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found.append((tag, attrs))

    handle_startendtag = handle_starttag


def _elements(html: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    parser = _Elements()
    parser.feed(html)
    parser.close()
    return parser.found


def _scheme(value: str | None) -> str:
    """Read a URL as a browser does: control characters and whitespace dropped, lower-cased.

    Returns:
        The value as a browser would match its scheme.

    """
    return "".join(ch for ch in value or "" if ch > " ").lower()


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_untrusted_markdown_renders_payloads_inert(payload: str) -> None:
    """No payload survives as a live tag, handler, style or dangerous URL.

    Handlers, styles and URLs live in attributes, so they are checked on the elements the
    HTML builds rather than as substrings: the words can survive as escaped *text*, which is
    inert -- Python-Markdown 3.11 escapes a tag it cannot parse rather than passing it through.
    Checked this way, every ``on*`` attribute is refused, not only the ones named, and an
    entity-encoded scheme (``&#106;avascript:``) is decoded before it is judged.
    """
    html = render_untrusted_markdown(payload)
    lowered = html.lower()

    for tag in FORBIDDEN:
        assert tag not in lowered, f"{tag!r} survived in {html!r}"
    for tag, attrs in _elements(html):
        assert tag not in FORBIDDEN_TAGS, f"<{tag}> survived in {html!r}"
        for name, value in attrs:
            assert not name.startswith("on"), f"handler {name!r} on <{tag}> in {html!r}"
            assert name != "style", f"style on <{tag}> in {html!r}"
            assert not _scheme(value).startswith(DANGEROUS_SCHEMES), f"{name}={value!r} on <{tag}> in {html!r}"


def test_a_tag_markdown_cannot_parse_comes_out_as_text() -> None:
    """The case the old substring test could not tell apart: the handler is words, not markup.

    Python-Markdown 3.11 no longer passes this malformed anchor through as HTML; it escapes it.
    Either way nothing in it may become an element with a handler.
    """
    html = render_untrusted_markdown('<a href="https://ok.test\\" onmouseover=\\"alert(1)">quote break</a>')

    assert all(not name.startswith("on") for _, attrs in _elements(html) for name, _ in attrs), html
    assert "quote break" in html


def test_the_element_check_sees_what_the_substring_check_missed() -> None:
    """Guard the guard: a handler or an encoded scheme in markup is caught, in text it is not."""
    live = '<a href="&#106;avascript:alert(1)" onfocus="x()">x</a>'
    inert = "<p>&lt;a onmouseover=&quot;alert(1)&quot;&gt;</p>"

    [(_, live_attrs)] = _elements(live)
    assert any(name.startswith("on") for name, _ in live_attrs)
    assert _scheme(dict(live_attrs)["href"]).startswith(DANGEROUS_SCHEMES)
    assert all(not name.startswith("on") for _, attrs in _elements(inert) for name, _ in attrs)


def test_untrusted_markdown_drops_only_the_href_of_a_dangerous_link() -> None:
    """A javascript: link keeps its text but loses the href entirely."""
    html = render_untrusted_markdown("[click me](javascript:alert(1))")

    assert "click me" in html
    assert "href" not in html


def test_untrusted_markdown_keeps_script_payload_out_of_the_text() -> None:
    """`script`/`style` subtrees are dropped whole, not unwrapped into visible text."""
    assert "alert(1)" not in render_untrusted_markdown("<script>alert(1)</script>")
    assert "display:none" not in render_untrusted_markdown("<style>body{display:none}</style>")


def test_untrusted_markdown_drops_images() -> None:
    """Remote images would report an admin reader's IP to a rider-chosen host."""
    html = render_untrusted_markdown("![alt text](https://tracker.test/pixel.png)")

    assert "<img" not in html
    assert "tracker.test" not in html


def test_untrusted_markdown_renders_ordinary_formatting() -> None:
    """Bold, italic, inline code, headings and line breaks still work."""
    html = render_untrusted_markdown("# Title\n\n**bold** and _em_ and `code`\n\nline one\nline two")

    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>em</em>" in html
    assert "<code>code</code>" in html
    assert "<br>" in html  # nl2br


def test_untrusted_markdown_renders_lists_tables_quotes_and_code_blocks() -> None:
    """The rest of the allowlist survives."""
    html = render_untrusted_markdown("- one\n- two")
    assert "<ul>" in html
    assert html.count("<li>") == 2

    html = render_untrusted_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<td>1</td>" in html

    assert "<blockquote>" in render_untrusted_markdown("> quoted")
    assert "<pre><code>" in render_untrusted_markdown("    indented_code()")


def test_untrusted_markdown_keeps_safe_links_and_hardens_them() -> None:
    """http/https/mailto links survive with rel hardening."""
    html = render_untrusted_markdown("[site](https://example.test/path?q=1)")
    assert 'href="https://example.test/path?q=1"' in html
    rel = html.split('rel="')[1].split('"')[0].split()
    assert set(rel) >= {"noopener", "noreferrer", "nofollow"}, html

    assert 'href="http://example.test/"' in render_untrusted_markdown("[site](http://example.test/)")
    assert 'href="mailto:team@example.test"' in render_untrusted_markdown("[mail](mailto:team@example.test)")
    # Site-relative links and same-page fragments carry no scheme and stay usable.
    assert 'href="/team/roster/"' in render_untrusted_markdown("[roster](/team/roster/)")


def test_untrusted_markdown_drops_target_and_class() -> None:
    """Attributes outside the allowlist go, even on an allowed tag."""
    html = render_untrusted_markdown('<a href="https://ok.test" target="_blank" class="btn">x</a>')

    assert "target=" not in html
    assert "class=" not in html
    assert 'href="https://ok.test"' in html


def test_untrusted_markdown_handles_empty_input() -> None:
    """Blank input renders nothing, like the trusted filter."""
    assert render_untrusted_markdown("") == ""
    assert render_untrusted_markdown(None) == ""


def test_filter_marks_sanitised_output_safe() -> None:
    """The template filter returns the sanitised HTML, marked safe."""
    out = render_markdown_untrusted("**bold** <script>alert(1)</script>")

    assert hasattr(out, "__html__")  # SafeString
    assert "<strong>bold</strong>" in out
    assert "<script" not in out


def test_trusted_filter_is_still_deliberately_unsanitised() -> None:
    """`render_markdown` keeps passing raw HTML through -- admin-authored only.

    Pinned so the distinction between the two filters stays a decision rather than
    an accident: if this ever starts failing because `render_markdown` gained a
    sanitiser, that is an improvement, but the trust comment above it must be
    updated to match, and user-authored content must still use the untrusted filter.
    """
    assert "<img" in render_markdown('<img src=x onerror="alert(1)">')
