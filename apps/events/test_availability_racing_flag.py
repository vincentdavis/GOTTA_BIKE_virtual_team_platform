"""The checkered flag on the availability results heatmap.

A rider picked for a race gets a flag beside their name in every slot of that day, because a
rider seldom races twice in one day: a captain filling another slot can see at a glance who
is already racing. The day is the race's day in the viewer's timezone -- the column its badge
sits in -- not its UTC date.
"""

from datetime import date, timedelta

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse

from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    AvailabilitySlotSelection,
    Event,
    Squad,
    SquadMember,
)


@pytest.fixture
def squad(db) -> Squad:
    """Build a squad in a visible event.

    Returns:
        The squad.

    """
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True)
    return Squad.objects.create(event=event, name="Affinity")


def _grid(squad: Squad, *, start_time: str = "19:00", end_time: str = "21:00") -> AvailabilityGrid:
    """Publish a three-day sheet of one-hour slots, set in UTC.

    Args:
        squad: The squad.
        start_time: First slot, UTC.
        end_time: End of the last slot, UTC.

    Returns:
        The grid.

    """
    return AvailabilityGrid.objects.create(
        squad=squad,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
        start_time=start_time,
        end_time=end_time,
        slot_duration=60,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _rider(user_model, grid: AvailabilityGrid, name: str, *cells: tuple[str, str]):
    """Add a squad member who marks some cells available.

    Args:
        user_model: The active user model.
        grid: The sheet.
        name: First name, also the username.
        *cells: The (date, time) cells the rider is free in, in UTC.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=name.lower(), email=f"{name.lower()}@example.test", first_name=name, last_name="Rider"
    )
    SquadMember.objects.create(squad=grid.squad, user=user, status=SquadMember.Status.MEMBER)
    AvailabilityResponse.objects.create(
        grid=grid, user=user, available_cells=[{"date": day, "time": time} for day, time in cells]
    )
    return user


def _race(grid: AvailabilityGrid, name: str, day: date, time: str, *riders, substitutes=()):
    """Schedule a race and pick its riders.

    Args:
        grid: The sheet.
        name: The race's name.
        day: Its UTC date.
        time: Its UTC time.
        *riders: The riders picked to race.
        substitutes: Its substitutes.

    Returns:
        The race.

    """
    race = AvailabilitySlotSelection.objects.create(grid=grid, name=name, slot_date=day, slot_time=time)
    race.selected_users.add(*riders)
    race.substitutes.add(*substitutes)
    return race


def _page(client, viewer, grid: AvailabilityGrid) -> BeautifulSoup:
    """Render the results page.

    Args:
        client: Test client.
        viewer: The signed-in user.
        grid: The sheet.

    Returns:
        The page, parsed.

    """
    client.force_login(viewer)
    response = client.get(
        reverse(
            "events:availability_results",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    )
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def _shown(page: BeautifulSoup) -> list[list[list[str]]]:
    """Read every lane of the heatmap, flag included.

    Args:
        page: The parsed results page.

    Returns:
        Per row, per day, per lane: a race badge, a rider's name with "🏁 " before it when
        they are flagged, or "" for a blank line.

    """
    rows = []
    for tr in page.select("#avail-grid tbody tr"):
        days = []
        for td in tr.select("td.heatmap-cell"):
            lanes = []
            for lane in td.find_all("div", class_="cell-lane", recursive=False):
                badge = lane.select_one(".slot-badge")
                name = lane.select_one("[data-user-tooltip] > a > span")
                if badge:
                    lanes.append(badge.get_text(strip=True))
                elif name:
                    flag = "🏁 " if lane.select_one(".cell-racing") else ""
                    lanes.append(flag + name.get_text(strip=True))
                else:
                    lanes.append("")
            days.append(lanes)
        rows.append(days)
    return rows


@pytest.mark.django_db
def test_a_picked_rider_is_flagged_in_every_slot_of_that_day(client, event_admin, user_model, squad):
    """Flagged at the race and in the day's other slots; not on another day, and not others."""
    grid = _grid(squad)
    ann = _rider(user_model, grid, "Ann", ("2026-07-01", "19:00"), ("2026-07-01", "20:00"), ("2026-07-02", "19:00"))
    _rider(user_model, grid, "Ben", ("2026-07-01", "19:00"))
    _race(grid, "Race 1", date(2026, 7, 1), "20:00", ann)

    page = _page(client, event_admin, grid)

    nineteen, twenty = _shown(page)
    assert nineteen == [["🏁 Ann Rider", "Ben Rider"], ["Ann Rider", ""], ["", ""]]
    assert twenty == [["Race 1", "🏁 Ann Rider"], ["", ""], ["", ""]]
    assert "Picked to race that day" in page.text


@pytest.mark.django_db
def test_a_substitute_is_not_flagged(client, event_admin, user_model, squad):
    """A substitute is not picked to race, so nothing is flagged and the legend stays out."""
    grid = _grid(squad)
    ann = _rider(user_model, grid, "Ann", ("2026-07-01", "19:00"))
    _race(grid, "Race 1", date(2026, 7, 1), "20:00", substitutes=[ann])

    page = _page(client, event_admin, grid)

    assert _shown(page)[0][0] == ["Ann Rider"]
    assert "Picked to race that day" not in page.text


@pytest.mark.django_db
def test_the_day_is_the_viewers_not_the_utc_date(client, event_admin, user_model, squad):
    """Seen from Berlin, a race at 22:00 UTC on 1 July falls on 2 July.

    So the rider is flagged at 23:00 on 2 July (21:00 UTC on 2 July, a different UTC date)
    and not at 23:00 on 1 July (21:00 UTC on 1 July, the race's own UTC date).
    """
    event_admin.timezone = "Europe/Berlin"
    event_admin.save(update_fields=["timezone"])
    grid = _grid(squad, start_time="21:00", end_time="23:00")
    ann = _rider(user_model, grid, "Ann", ("2026-07-01", "21:00"), ("2026-07-01", "22:00"), ("2026-07-02", "21:00"))
    _race(grid, "Race 1", date(2026, 7, 1), "22:00", ann)

    midnight, eleven = _shown(_page(client, event_admin, grid))

    # Columns are 1 to 4 July, Berlin time: the last UTC evening runs past midnight there.
    assert midnight == [["", ""], ["Race 1", "🏁 Ann Rider"], ["", ""], ["", ""]]
    assert eleven == [["Ann Rider"], ["🏁 Ann Rider"], [""], [""]]


@pytest.mark.django_db
def test_the_flag_names_the_race(client, event_admin, user_model, squad):
    """Hover text and screen readers get the race's name; two races that day, both names."""
    grid = _grid(squad)
    ann = _rider(user_model, grid, "Ann", ("2026-07-01", "19:00"), ("2026-07-01", "20:00"))
    _race(grid, "Race 1", date(2026, 7, 1), "19:00", ann)
    _race(grid, "Q&A <2>", date(2026, 7, 1), "20:00", ann)

    flag = _page(client, event_admin, grid).select_one(".cell-racing")

    assert flag["title"] == "Racing Race 1, Q&A <2>"
    assert flag["aria-label"] == flag["title"]
    assert flag["role"] == "img"
