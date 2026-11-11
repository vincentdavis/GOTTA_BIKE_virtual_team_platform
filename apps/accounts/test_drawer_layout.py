"""Guards for base.html's layout: the page has one scroller, and the document never scrolls.

``.drawer-content`` scrolls the page at every width. Two things let the document scroll as
well, so pages scrolled in two places and their bottom, with anything stuck to it, sat
off-screen:

- From ``lg`` the open drawer's side sits in the drawer's grid row, and daisyUI gives it
  ``height: 100dvh``. That made the row, and the ``h-full`` scroller beside it, a whole window
  tall below the header and banners. ``lg:h-full`` holds the side to the row.
- An absolutely positioned element with no positioned ancestor in the page (any ``sr-only``
  span) was placed against ``.drawer``, outside the scroller, so the scroller neither clipped
  nor counted it. ``relative`` makes the scroller its containing block.

Both were measured in a browser. These tests only keep the classes from being dropped.
"""

import re

import pytest
from django.urls import reverse


def _classes(html: str, first: str) -> set[str]:
    """Read the class list of the element whose class attribute starts with ``first``.

    Returns:
        The classes.

    """
    match = re.search(rf'class="({re.escape(first)}(?: [^"]*)?)"', html)
    assert match, f"no element with class {first!r} on the page"
    return set(match.group(1).split())


@pytest.fixture
def page(auth_client) -> str:
    """Render a page with the sidebar, which only signed-in members get.

    Returns:
        The page's HTML.

    """
    return auth_client.get(reverse("team:roster")).content.decode()


@pytest.mark.django_db
def test_the_open_sidebar_is_held_to_the_space_under_the_header(page):
    assert "lg:h-full" in _classes(page, "drawer-side")


@pytest.mark.django_db
def test_the_scroller_contains_what_it_scrolls(page):
    assert {"relative", "overflow-y-auto", "h-full"} <= _classes(page, "drawer-content")
