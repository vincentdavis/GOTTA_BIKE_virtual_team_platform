"""Guards for the site header at the larger text sizes.

Everything in base.html's header is rem, so it grows with the text-size preference (and with a
browser's own default font size). At the largest step a wide logo and the team name needed
493px on a 375px phone, and a logo whose file failed to load 422px: the avatar menu was pushed
off the right edge, and the page scrolled sideways (WCAG 1.4.10 Reflow).

The home link now gives way instead: its wrapper may shrink (``min-w-0``), the link stays
inside it (``max-w-full``), the logo can shrink, a name on its own is cut to one line, and a
name beside a logo shows only when it fits whole. The widths were measured in a browser at
320, 375, 640, 768, 1024 and 1280px at every text size, in both themes; these tests hold the
markup that does it.
"""

import re

import pytest
from constance.test import override_config
from django.core.cache import cache
from django.urls import reverse

from gotta_bike_platform.models import SiteSettings

_TEAM = "Test Coalition"


@pytest.fixture(autouse=True)
def _clear_cache():
    """SiteSettings memoises into a process-wide LocMemCache that pytest-django never resets."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def logo(db):
    """Put a logo on the singleton. Only its name is stored: the header never opens the file.

    Returns:
        The SiteSettings row, now with a logo.

    """
    settings_row = SiteSettings.get_settings()
    settings_row.site_logo.name = "site/logo.png"
    settings_row.save()
    return settings_row


def _header(client) -> str:
    html = client.get(reverse("account_login")).content.decode()
    return html[html.index("<header") : html.index("</header>")]


def _home_link(header: str) -> str:
    match = re.search(r'<a href="/" class="btn btn-ghost text-xl max-w-full">(.*?)</a>', header, re.S)
    assert match, "the home link lost max-w-full, so it can outgrow its wrapper again"
    return match.group(1)


@pytest.mark.django_db
def test_the_home_link_gives_way_to_the_avatar_menu(auth_client):
    """The link's side shrinks; the avatar side does not, so the menu is always on screen."""
    html = auth_client.get(reverse("team:roster")).content.decode()
    header = html[html.index("<header") : html.index("</header>")]

    assert '<div class="flex-1 min-w-0">\n    <a href="/" class="btn btn-ghost text-xl max-w-full">' in header
    assert '<div class="flex-none gap-2 px-4">' in header
    assert 'id="textsize-largest"' in header  # the avatar menu is in this header, not elsewhere


@pytest.mark.django_db
def test_a_name_on_its_own_stays_on_one_line(client):
    """The button's height is fixed, so a wrapped name spilled out of it over the page."""
    with override_config(LOGO_DISPLAY_MODE="name_only", TEAM_NAME=_TEAM):
        link = _home_link(_header(client))

    assert f'<span class="truncate">{_TEAM}</span>' in link
    assert "<img" not in link


@pytest.mark.django_db
def test_beside_a_logo_the_name_shows_only_when_it_fits_whole(client, logo):
    """A one-line box that wraps and clips: a name with no room drops out of view, whole."""
    with override_config(LOGO_DISPLAY_MODE="logo_and_name", TEAM_NAME=_TEAM):
        link = _home_link(_header(client))

    assert '<span class="flex flex-wrap items-center gap-x-1.5 h-8 min-w-0 overflow-hidden">' in link
    assert f'<span class="whitespace-nowrap">{_TEAM}</span>' in link
    assert 'class="h-8 max-w-32 min-w-0 object-contain"' in link  # a wide logo can shrink too


@pytest.mark.django_db
def test_beside_the_name_the_logo_is_not_read_out_as_well(client, logo):
    """The clipped name is still in the link's accessible name, so the logo's alt is empty.

    With the alt as well, a screen reader announced the link as the team name twice.
    """
    with override_config(LOGO_DISPLAY_MODE="logo_and_name", TEAM_NAME=_TEAM):
        link = _home_link(_header(client))

    assert 'alt=""' in link
    assert link.count(_TEAM) == 1


@pytest.mark.django_db
def test_a_logo_on_its_own_names_the_link_and_can_shrink(client, logo):
    with override_config(LOGO_DISPLAY_MODE="logo_only", TEAM_NAME=_TEAM):
        link = _home_link(_header(client))

    assert f'alt="{_TEAM}" class="h-8 max-w-32 min-w-0 object-contain"' in link
    assert "<span" not in link


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["name_only", "logo_only", "logo_and_name", "Logo_Only"])
def test_without_a_logo_every_mode_shows_the_name(client, mode):
    with override_config(LOGO_DISPLAY_MODE=mode, TEAM_NAME=_TEAM):
        link = _home_link(_header(client))

    assert f'<span class="truncate">{_TEAM}</span>' in link


@pytest.mark.django_db
def test_a_mistyped_mode_never_leaves_the_link_empty(client, logo):
    """LOGO_DISPLAY_MODE is free text. A typo used to show neither the logo nor the name."""
    with override_config(LOGO_DISPLAY_MODE="Logo_Only", TEAM_NAME=_TEAM):
        link = _home_link(_header(client))

    assert f'<span class="truncate">{_TEAM}</span>' in link
