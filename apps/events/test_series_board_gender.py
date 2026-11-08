"""The series board's squad gender filter, on the board's own page and in the event page's tab.

A squad's gender is Male, Female, COED or unset. The board offers each gender its squads use,
with how many, and choosing one narrows the whole board to those squads: its rows, each
stage's "N of M racing" and Needs attention. A choice swaps the board in place, in the tab
and on the page, keeps focus on the radio by its id, and says what the board now shows from
a live region outside the swapped part.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, SeriesStage, Squad
from apps.events.series_views import BOARD_PAGE_ID, BOARD_PANEL_ID

TAB = {"HX-Request": "true", "HX-Target": BOARD_PANEL_ID}
IN_PLACE = {"HX-Request": "true", "HX-Target": BOARD_PAGE_ID}
ANNOUNCER = re.compile(r'<p id="series-board-announcer" hx-swap-oob="innerHTML">(.*?)</p>', re.S)


@pytest.fixture
def event(db) -> Event:
    """Build a series with one stage and four squads: Male, two Female, and one with no gender.

    Returns:
        The event.

    """
    today = date.today()
    event = Event.objects.create(
        title="Winter Series",
        start_date=today,
        end_date=today + timedelta(days=60),
        visible=True,
        availability_type=Event.AvailabilityType.SERIES,
    )
    SeriesStage.objects.create(event=event, order=0, name="Opener", stage_type="points")
    for name, gender in (("A1", "Male"), ("B1", "Female"), ("B2", "Female"), ("C1", "")):
        Squad.objects.create(event=event, name=name, gender=gender)
    return event


def _url(event: Event) -> str:
    return reverse("events:series_board", args=[event.pk])


def _names(response) -> list[str]:
    return [row["squad"].name for row in response.context["rows"]]


def _checked(body: str, radio_id: str) -> bool:
    return re.search(rf'<input type="radio" id="{radio_id}"[^>]*checked', body) is not None


@pytest.mark.django_db
def test_the_board_offers_the_genders_its_squads_use(client, event, event_admin):
    client.force_login(event_admin)

    body = client.get(_url(event)).content.decode()

    assert "<legend" in body
    assert "Squad gender" in body
    for radio_id, label in (("all", "All (4)"), ("male", "Male (1)"), ("female", "Female (2)")):
        assert f'id="board-gender-{radio_id}"' in body
        assert label in body
    # No squad is COED, so there is nothing to choose there.
    assert 'id="board-gender-coed"' not in body
    assert _checked(body, "board-gender-all")
    # Each squad says its gender beside its rider count; one without a gender gives only the count.
    assert "Male &middot; 0 riders" in body
    assert "Female &middot; 0 riders" in body


@pytest.mark.django_db
def test_choosing_a_gender_narrows_the_whole_board(client, event, event_admin):
    client.force_login(event_admin)

    response = client.get(_url(event), {"gender": "female"})

    assert _names(response) == ["B1", "B2"]
    assert response.context["columns"][0]["racing"] == "0 of 2 racing"
    # No squad has stage times yet, so the two shown, and only they, need attention.
    assert [item["text"] for item in response.context["attention"]] == [
        "B1 hasn't set its stage times yet.",
        "B2 hasn't set its stage times yet.",
    ]
    body = response.content.decode()
    assert "Winter Series &middot; 2 Female squads of 4 &middot; 1 stage" in body
    assert _checked(body, "board-gender-female")
    assert not _checked(body, "board-gender-all")


@pytest.mark.django_db
def test_the_gender_is_read_whatever_its_case(client, event, event_admin):
    client.force_login(event_admin)

    assert _names(client.get(_url(event), {"gender": " Male "})) == ["A1"]


@pytest.mark.django_db
@pytest.mark.parametrize("asked", ["coed", "mixed", ""])
def test_a_gender_no_squad_has_shows_every_squad(client, event, event_admin, asked):
    client.force_login(event_admin)

    response = client.get(_url(event), {"gender": asked})

    assert _names(response) == ["A1", "B1", "B2", "C1"]
    assert response.context["gender"] == ""
    assert _checked(response.content.decode(), "board-gender-all")


@pytest.mark.django_db
def test_no_gender_choice_when_no_squad_has_a_gender(client, event, event_admin):
    Squad.objects.filter(event=event).update(gender="")
    client.force_login(event_admin)

    response = client.get(_url(event), {"gender": "female"})

    assert 'name="gender"' not in response.content.decode()
    assert len(_names(response)) == 4


@pytest.mark.django_db
def test_the_tab_swaps_itself_and_says_what_it_now_shows(client, event, event_admin):
    client.force_login(event_admin)

    opened = client.get(_url(event), headers=TAB)
    response = client.get(_url(event), {"gender": "female", "tz": "mine"}, headers=TAB)

    # Opening the tab is not a choice, so it announces nothing.
    assert "series-board-announcer" not in opened.content.decode()
    assert response.context["embedded"] is True
    assert _names(response) == ["B1", "B2"]
    body = response.content.decode()
    form = re.search(r"<form[^>]*>", body).group(0)
    assert f'hx-target="#{BOARD_PANEL_ID}"' in form
    assert _checked(body, "board-gender-female")
    assert _checked(body, "board-tz-mine")
    assert ANNOUNCER.search(body).group(1) == "2 Female squads of 4 shown, times in your timezone."
    # The tab sits on the event page, whose address is not the board's.
    assert "HX-Replace-Url" not in response


@pytest.mark.django_db
def test_the_event_page_keeps_the_announcer_outside_the_panel_it_replaces(client, event, event_admin):
    client.force_login(event_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    announcer = re.search(r'<p id="series-board-announcer" class="sr-only" role="status"', body)
    assert announcer is not None
    assert announcer.start() < body.index(f'<div id="{BOARD_PANEL_ID}"')


@pytest.mark.django_db
def test_the_page_swaps_the_board_in_place_and_keeps_its_address(client, event, event_admin):
    client.force_login(event_admin)

    page = client.get(_url(event)).content.decode()
    response = client.get(_url(event), {"gender": "female", "tz": ""}, headers=IN_PLACE)
    every = client.get(_url(event), {"gender": "", "tz": "mine"}, headers=IN_PLACE)

    announcer = re.search(r'<p id="series-board-announcer" class="sr-only" role="status"', page)
    assert announcer is not None
    assert announcer.start() < page.index(f'<div id="{BOARD_PAGE_ID}">')
    templates = {template.name for template in response.templates}
    assert "events/_series_board.html" in templates
    assert "base.html" not in templates
    assert response.context["embedded"] is False
    body = response.content.decode()
    assert "<h1" in body
    assert f'hx-target="#{BOARD_PAGE_ID}"' in re.search(r"<form[^>]*>", body).group(0)
    assert ANNOUNCER.search(body).group(1) == "2 Female squads of 4 shown, times in each squad's own timezone."
    # The address keeps only what differs from the default, so All gives back the plain URL.
    assert response["HX-Replace-Url"] == f"{_url(event)}?gender=female"
    assert every["HX-Replace-Url"] == f"{_url(event)}?tz=mine"
    assert ANNOUNCER.search(every.content.decode()).group(1) == "4 squads shown, times in your timezone."


@pytest.mark.django_db
def test_an_in_place_swap_is_refused_rather_than_redirected(client, event, team_member):
    client.force_login(team_member)

    response = client.get(_url(event), {"gender": "female"}, headers=IN_PLACE)

    # htmx follows a redirect, and would put the event page where the board was.
    assert response.status_code == 403
