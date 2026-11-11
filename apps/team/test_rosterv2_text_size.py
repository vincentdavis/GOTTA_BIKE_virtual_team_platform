"""Guards for the card roster's columns at the larger text sizes.

The columns used to follow the screen alone (four from 1280px). Breakpoints are media queries,
whose rem do not grow with the text-size preference, so at the largest size at 1280px four
columns left each rider's name 35px wide, a few letters to a line.

Now N columns also need room for N cards of 13rem and their gaps, measured on a container
around the list: a container query's rem do grow with the text size, so columns drop as the
text grows (4, 3, 3, 2 across the four sizes at 1280px), while the breakpoints still cap the
count, so the default size lays out as before. Measured in a browser at 320, 375, 768, 1024 and
1280px; these tests hold the markup.
"""

import re

import pytest
from django.urls import reverse

_CARD_REM = 13
_GAP_REM = 0.75  # gap-3


def _list_and_wrapper(client) -> tuple[str, str]:
    html = client.get(reverse("team:roster")).content.decode()
    match = re.search(r'<div class="([^"]*)">\s*<ul id="roster-list" class="([^"]*)">', html)
    assert match, "the card list is no longer inside its own query container"
    return match.group(1), match.group(2)


@pytest.mark.django_db
def test_the_card_list_sits_in_a_query_container(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    wrapper, _ = _list_and_wrapper(auth_client)

    assert "@container" in wrapper.split()


@pytest.mark.django_db
def test_columns_need_room_for_the_cards_not_just_a_wide_screen(auth_client, roster_rider):
    """No column count is left to a breakpoint alone; each also asks the container."""
    roster_rider(zwid=1001, name="Ada Racer")

    _, classes = _list_and_wrapper(auth_client)
    columns = classes.split()

    assert "gap-3" in columns
    assert "grid-cols-1" in columns
    for bare in ("sm:grid-cols-2", "lg:grid-cols-3", "xl:grid-cols-4"):
        assert bare not in columns, f"{bare} ignores the text size again"
    # The breakpoints still cap the count, so the default text size is laid out as before.
    capped = dict(re.findall(r"(sm|lg|xl):@min-\[[\d.]+rem\]:grid-cols-(\d)", classes))
    assert capped == {"sm": "2", "lg": "3", "xl": "4"}


@pytest.mark.django_db
def test_each_threshold_is_that_many_cards_and_their_gaps(auth_client, roster_rider):
    """Change the card's minimum, or the gap, and every threshold has to move with it."""
    roster_rider(zwid=1001, name="Ada Racer")

    _, classes = _list_and_wrapper(auth_client)

    thresholds = re.findall(r"@min-\[([\d.]+)rem\]:grid-cols-(\d)", classes)
    assert len(thresholds) == 3
    for width, count in thresholds:
        n = int(count)
        assert float(width) == pytest.approx(n * _CARD_REM + (n - 1) * _GAP_REM), f"{n} columns at {width}rem"
