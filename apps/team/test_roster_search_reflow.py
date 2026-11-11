"""Guard for the card roster's search field at the largest text size.

The field's wrapper asked for ``min-w-48``: 12rem, which is 288px at the largest text size
(150%). On a 320px phone the search form has about 200px, so the field pushed
``.drawer-content`` 28px wider than the screen and the page scrolled sideways (WCAG 1.4.10
Reflow). It now asks for 12rem or the whole row, whichever is less, and the Search button
wraps below it when the two do not fit.

Measured in a browser at 320, 375 and 1024px at every text size, with the filters open and
closed: no overflow, and the field and button still share a row wherever they used to. This
test keeps the class from being put back.
"""

import re

import pytest
from django.urls import reverse


def _search_field_wrapper_classes(html: str) -> list[str]:
    """Read the classes of the element wrapping the search field's label and input.

    Returns:
        The wrapper's classes.

    """
    start = html.index('role="search"')
    form = html[start : html.index("</form>", start)]
    match = re.search(r'<div class="([^"]*)">\s*<label class="label py-1" for="roster-search">', form)
    assert match, "the search field's wrapper moved; find it again before trusting this test"
    return match.group(1).split()


@pytest.mark.django_db
def test_the_search_field_never_asks_for_more_than_its_row(auth_client):
    classes = _search_field_wrapper_classes(auth_client.get(reverse("team:roster")).content.decode())

    assert "min-w-[min(12rem,100%)]" in classes
    assert "flex-1" in classes  # it still takes the room the button leaves


@pytest.mark.django_db
def test_no_fixed_minimum_width_is_left_on_the_field(auth_client):
    """A rem minimum grows with the text size, so on its own it outgrows a phone."""
    classes = _search_field_wrapper_classes(auth_client.get(reverse("team:roster")).content.decode())

    assert not [c for c in classes if re.fullmatch(r"min-w-(\d+|\[\d[^\]]*\])", c)]
