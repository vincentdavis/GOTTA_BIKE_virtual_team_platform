"""Markdown rendering for text written by ordinary users.

:func:`render_markdown` (``accounts_tags``) renders markdown and marks the result
safe without sanitising it. Python-Markdown passes raw HTML through untouched, so
that filter is only sound for admin-authored text (CMS pages, announcements, event
descriptions, signup-question labels).

Anything a rider can type -- ticket details and resolutions, membership-application
messages, availability-grid descriptions -- goes through :func:`render_untrusted_markdown`
instead: markdown first, then an allowlist sanitiser, so a stored payload cannot run
in the browser of whoever reads it (frequently an admin).

The allowlist is deliberately narrow: basic formatting, lists, tables, code,
blockquotes and links. Notably absent:

* ``img`` -- markdown image syntax is dropped. A remote image in rider-authored text
  would report the reader's IP and user agent to an arbitrary third-party host every
  time an admin opened the page.
* ``style`` attributes and every ``on*`` handler -- neither is on the allowlist, and
  ``allowed_css_properties`` is empty, so inline CSS is dropped even if ``style``
  were ever allowed.
* ``script`` / ``style`` subtrees, which are dropped whole rather than unwrapped, so
  their text payload is not re-emitted as visible content.

Links keep only ``http``, ``https`` and ``mailto`` URLs (plus site-relative ones and
same-page fragments, which carry no scheme); anything else -- ``javascript:``,
``data:``, ``vbscript:`` -- loses its ``href`` and the anchor is left inert. Surviving
links are hardened with ``rel="noopener noreferrer nofollow"``.
"""

import markdown
from justhtml import JustHTML, SanitizationPolicy, UrlPolicy, UrlRule

#: Extensions shared with the trusted ``render_markdown`` filter, so user-authored
#: text renders the same way admin-authored text does.
MARKDOWN_EXTENSIONS = ("nl2br", "sane_lists", "tables")

_ALLOWED_TAGS = frozenset(
    {
        # Block + inline formatting
        "p", "br", "hr", "blockquote", "pre", "code",
        "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "small", "sub", "sup",
        # Headings
        "h1", "h2", "h3", "h4", "h5", "h6",
        # Lists
        "ul", "ol", "li",
        # Tables
        "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
        # Links
        "a",
    },
)

#: Containers whose text payload must not survive as visible content.
_DROP_CONTENT_TAGS = frozenset(
    {"script", "style", "svg", "math", "iframe", "object", "embed", "template", "noscript", "textarea", "title"},
)

UNTRUSTED_HTML_POLICY = SanitizationPolicy(
    allowed_tags=_ALLOWED_TAGS,
    # Every tag not listed here gets no attributes at all -- which is what keeps
    # `on*` handlers, `style`, `target` and `class` out without enumerating them.
    allowed_attributes={
        "a": frozenset({"href", "title"}),
        "th": frozenset({"colspan", "rowspan"}),
        "td": frozenset({"colspan", "rowspan"}),
    },
    url_policy=UrlPolicy(
        # Deny by default: a URL attribute with no rule below is dropped.
        default_handling="strip",
        allow_rules={
            # `handling="allow"` is what lets a *valid* URL through; without it the
            # policy default ("strip") would drop even an allowed https href.
            ("a", "href"): UrlRule(allowed_schemes=frozenset({"http", "https", "mailto"}), handling="allow"),
        },
    ),
    force_link_rel=frozenset({"noopener", "noreferrer", "nofollow"}),
    drop_content_tags=_DROP_CONTENT_TAGS,
    allowed_css_properties=frozenset(),
    drop_comments=True,
    drop_doctype=True,
    # Keep the text inside a disallowed tag, drop the tag itself.
    disallowed_tag_handling="unwrap",
    # Remove/drop unsafe constructs and keep going, rather than raising mid-render.
    unsafe_handling="strip",
)


def render_untrusted_markdown(value: str) -> str:
    """Render user-authored markdown and sanitise the resulting HTML.

    Args:
        value: Markdown text written by an ordinary user.

    Returns:
        Sanitised HTML. Not marked safe -- callers that put it in a template must
        do that themselves (see the ``render_markdown_untrusted`` filter).

    """
    if not value:
        return ""
    html = markdown.markdown(value, extensions=list(MARKDOWN_EXTENSIONS))
    return JustHTML(html, fragment=True, sanitize=True, policy=UNTRUSTED_HTML_POLICY).to_html(pretty=False)
