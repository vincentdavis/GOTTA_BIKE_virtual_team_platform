"""The search on an event's All Races tab.

The list is paginated on the server, so the search runs there: it keeps a race whose name,
squad's name or a picked rider's name contains the text. A live search (htmx, as you type)
gets the results region alone, and a race found by one of its riders opens its rider list,
so the reason for every match is on screen.
"""

from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse
from django.utils import timezone

from apps.events.models import AvailabilityGrid, AvailabilitySlotSelection, Event, Squad

LIVE = {"HX-Request": "true", "HX-Target": "all-races-results"}


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = timezone.now().date()
    return Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=60), visible=True)


def _race(event: Event, squad_name: str, name: str, days_ahead: int, *riders, time: str = "19:00"):
    """Schedule an upcoming race for a squad, creating the squad and its sheet as needed.

    Args:
        event: The event.
        squad_name: The squad's name.
        name: The race's name.
        days_ahead: How many days from today it runs.
        *riders: The riders picked for it.
        time: Its UTC start time.

    Returns:
        The race.

    """
    squad, _ = Squad.objects.get_or_create(event=event, name=squad_name)
    today = timezone.now().date()
    grid, _ = AvailabilityGrid.objects.get_or_create(
        squad=squad,
        defaults={
            "start_date": today,
            "end_date": today + timedelta(days=60),
            "start_time": "00:00",
            "end_time": "23:30",
            "slot_duration": 30,
            "grid_timezone": "UTC",
            "status": AvailabilityGrid.Status.PUBLISHED,
        },
    )
    race = AvailabilitySlotSelection.objects.create(
        grid=grid, name=name, slot_date=today + timedelta(days=days_ahead), slot_time=time
    )
    race.selected_users.add(*riders)
    return race


@pytest.fixture
def races(event, user_model) -> dict:
    """Five races, four of which "fran" should find -- each in a different way.

    Returns:
        The races by name.

    """

    def rider(username, first="", last="", discord=""):
        return user_model.objects.create_user(
            username=username,
            email=f"{username}@example.test",
            first_name=first,
            last_name=last,
            discord_username=discord,
        )

    return {
        race.name: race
        for race in [
            _race(event, "Frantic Pace", "Race 1", 1),  # the squad's name
            _race(event, "Alpha", "Race 2", 2, rider("francois", "François", "Demo")),  # a rider, accented
            # Sam's Discord name holds "fran", but the card shows "Sam Smith": no match.
            _race(event, "Bravo", "Race 3", 3, rider("sam", "Sam", "Smith", discord="frankie")),
            _race(event, "Charlie", "Race 4", 4, rider("nameless", discord="frankie99")),  # the name shown
            _race(event, "Delta", "Fran Cup", 5),  # the race's name
        ]
    }


def _get(client, viewer, event, query: str = "", **headers):
    """Load the All Races tab.

    Args:
        client: Test client.
        viewer: The signed-in user.
        event: The event.
        query: The search text, if any.
        **headers: Request headers.

    Returns:
        The response.

    """
    client.force_login(viewer)
    url = reverse("events:event_all_races", args=[event.pk])
    response = client.get(url, {"q": query} if query else {}, headers=headers)
    assert response.status_code == 200
    return response


def _names(response) -> list[str]:
    """Name the races listed, in order.

    Returns:
        Their names.

    """
    return [slot["selection"].name for slot in response.context["slots"]]


@pytest.mark.django_db
def test_without_a_search_every_upcoming_race_is_listed(client, event_admin, event, races):
    """The page before this change, unchanged."""
    response = _get(client, event_admin, event)

    assert _names(response) == ["Race 1", "Race 2", "Race 3", "Race 4", "Fran Cup"]
    assert response.context["total_races"] == 5
    assert not any(slot["open_riders"] for slot in response.context["slots"])


@pytest.mark.django_db
def test_the_search_finds_a_race_by_its_name_its_squad_or_a_rider(client, event_admin, event, races):
    """Case and accents ignored; a rider is matched by the name the card shows."""
    response = _get(client, event_admin, event, "fran")

    assert _names(response) == ["Race 1", "Race 2", "Race 4", "Fran Cup"]
    assert _names(_get(client, event_admin, event, "FRANCOIS")) == ["Race 2"]
    assert response.context["total_races"] == 5


@pytest.mark.django_db
def test_a_race_found_by_a_rider_opens_its_rider_list(client, event_admin, event, races):
    """Otherwise the reason it matched would be hidden behind the toggle."""
    response = _get(client, event_admin, event, "fran")

    assert {slot["selection"].name for slot in response.context["slots"] if slot["open_riders"]} == {"Race 2", "Race 4"}
    page = BeautifulSoup(response.content, "html.parser")
    assert page.select_one(f"#all-races-riders-{races['Race 2'].pk}").get("style") is None
    assert page.select_one(f"#all-races-riders-{races['Race 1'].pk}")["style"] == "display:none"
    # The toggle says which state its list is in, since it no longer always starts closed.
    assert page.select_one(f'[aria-controls="all-races-riders-{races["Race 2"].pk}"]')["aria-expanded"] == "true"
    assert page.select_one(f'[aria-controls="all-races-riders-{races["Race 1"].pk}"]')["aria-expanded"] == "false"


@pytest.mark.django_db
def test_a_live_search_gets_the_results_region_alone(client, event_admin, event, races):
    """No participation report on every keystroke; the address bar follows the search."""
    response = _get(client, event_admin, event, "fran", **LIVE)

    rendered = [template.name for template in response.templates]
    assert "events/_all_races_results.html" in rendered
    assert "events/event_all_races.html" not in rendered
    assert response["HX-Replace-Url"] == reverse("events:event_all_races", args=[event.pk]) + "?q=fran"
    assert "HX-Request" in response["Vary"]
    announcer = BeautifulSoup(response.content, "html.parser").select_one("#all-races-announcer")
    assert announcer["hx-swap-oob"] == "innerHTML"
    assert announcer.get_text(strip=True) == "4 of 5 races match."


@pytest.mark.django_db
def test_clearing_the_box_gives_back_the_plain_address(client, event_admin, event, races):
    """An empty search is no search."""
    response = _get(client, event_admin, event, "", **LIVE)

    assert response["HX-Replace-Url"] == reverse("events:event_all_races", args=[event.pk])
    assert len(_names(response)) == 5


@pytest.mark.django_db
def test_a_history_restore_gets_the_whole_page(client, event_admin, event, races):
    """Going Back with no copy saved, htmx asks for the page: a fragment would replace it."""
    response = _get(client, event_admin, event, "fran", **{"HX-Request": "true", "HX-History-Restore-Request": "true"})

    assert "events/event_all_races.html" in [template.name for template in response.templates]


@pytest.mark.django_db
def test_paging_keeps_the_search(client, event_admin, event, races):
    """Page 2 of a search must not silently become page 2 of everything."""
    for minute in range(26):
        _race(event, "Frantic Pace", f"Heat {minute}", 6, time=f"20:{minute:02d}")

    response = _get(client, event_admin, event, "frantic")

    assert response.context["paginator"].count == 27
    assert "?page=2&amp;q=frantic" in response.content.decode()


@pytest.mark.django_db
def test_nothing_found_says_so(client, event_admin, event, races):
    """An empty list with no word would look broken."""
    response = _get(client, event_admin, event, "zzz")

    assert _names(response) == []
    assert "No upcoming races match your search." in response.content.decode()
