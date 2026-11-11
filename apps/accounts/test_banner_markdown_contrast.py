"""Guards for the contrast of the two Markdown banners at the top of every page (base.html).

Both render admin-written Markdown with ``prose``, and daisyUI colours prose's bold, headings,
table headers, quotes, kbd and list markers from ``--color-base-content``. On a coloured
banner that is unreadable (WCAG 1.4.3). Measured in a browser:

- The site announcement (on primary): bold, headings, quotes and table headers 2.1:1 and kbd
  1.9:1 in the light theme. And daisyUI's dark primary is too light for its own content colour,
  so in the dark theme every word was 4.13:1, the plain text included.
- The Zwift connect banner (on info): ``.prose`` sets its own text colour, so in the dark theme
  the whole message was 1.76:1 and its bold 2.07:1.

Each of those elements now follows the banner's own text colour, and the announcement's
surface is primary with a tenth of black. Every element then measured 8.1:1 (light) and 5.2:1
(dark) on the announcement, and 6.3:1 in both themes on the Zwift banner. Code and mark bring
their own background and pass as they are, so they are left alone.

The site-wide focus ring (a11y.css, base-content) had the same problem on the banners: 2.1:1
on the announcement in light (1.8:1 once it was darkened) and 2.1:1 on the Zwift banner in
dark. It takes each banner's text colour there too, measured with keyboard focus at the same
ratios as the text.
"""

import re
from pathlib import Path

import pytest
from constance.test import override_config
from django.urls import reverse

_A11Y_CSS = Path(__file__).resolve().parent.parent.parent / "theme/static/css/a11y.css"

# The prose elements daisyUI colours from base-content. ``headings`` covers h1-h6 and th.
_FOLLOW_THE_BANNER = ("a", "strong", "headings", "blockquote", "kbd")


def _announcement_classes(html: str) -> set[str]:
    """Read the class list of the site announcement.

    Returns:
        The announcement's classes.

    """
    match = re.search(r'id="site-announcement"\s+class="([^"]*)"', html)
    assert match, "the site announcement lost its id; find it again before trusting this test"
    return set(match.group(1).split())


def _zauth_message_classes(html: str) -> set[str]:
    """Read the class list of the element that renders the Zwift banner's Markdown message.

    Returns:
        The message's classes.

    """
    banner = html[html.index('id="zauth-banner"') :]
    match = re.search(r'class="(prose [^"]*)"', banner)
    assert match, "the Zwift banner's message moved; find it again before trusting this test"
    return set(match.group(1).split())


@pytest.mark.django_db
@override_config(SITE_ANNOUNCEMENT="**Racing** this weekend")
def test_every_prose_colour_in_the_announcement_follows_its_text(client, discord_app):
    classes = _announcement_classes(client.get(reverse("account_login")).content.decode())

    assert {"prose", "text-primary-content", "marker:text-primary-content"} <= classes
    for element in _FOLLOW_THE_BANNER:
        assert f"prose-{element}:text-primary-content" in classes, element


@pytest.mark.django_db
@override_config(SITE_ANNOUNCEMENT="**Racing** this weekend")
def test_the_announcement_surface_is_dark_enough_in_both_themes(client, discord_app):
    """Plain primary under primary-content is 4.13:1 in daisyUI's dark theme."""
    classes = _announcement_classes(client.get(reverse("account_login")).content.decode())

    assert "bg-[color-mix(in_oklab,var(--color-primary)_90%,black)]" in classes
    assert "bg-primary" not in classes


@pytest.mark.django_db
@override_config(SITE_ANNOUNCEMENT="")
def test_no_announcement_no_banner(client, discord_app):
    assert 'id="site-announcement"' not in client.get(reverse("account_login")).content.decode()


@pytest.mark.django_db
@override_config(ZAUTH_BANNER_ENABLED=True)
def test_every_prose_colour_in_the_zwift_banner_follows_its_text(auth_client):
    """``.prose`` sets its own colour, so the message has to name the banner's, not inherit it."""
    classes = _zauth_message_classes(auth_client.get(reverse("team:roster")).content.decode())

    assert {"text-info-content", "marker:text-info-content"} <= classes
    for element in _FOLLOW_THE_BANNER:
        assert f"prose-{element}:text-info-content" in classes, element


@pytest.mark.parametrize(
    ("banner", "colour"),
    [("site-announcement", "--color-primary-content"), ("zauth-banner", "--color-info-content")],
)
def test_the_focus_ring_on_a_banner_takes_the_banner_text_colour(banner, colour):
    """The site-wide ring is base-content: about 2:1 on each banner in one of the themes.

    a11y.css forces that ring with !important, so the banner's rule has to as well, and wins on
    specificity (an id).
    """
    css = re.sub(r"/\*.*?\*/", "", _A11Y_CSS.read_text(), flags=re.S)
    rule = re.search(rf"#{banner} :focus-visible\s*\{{([^}}]*)\}}", css)

    assert rule, f"no focus ring rule for #{banner}"
    assert f"outline-color: var({colour}) !important;" in rule.group(1)
