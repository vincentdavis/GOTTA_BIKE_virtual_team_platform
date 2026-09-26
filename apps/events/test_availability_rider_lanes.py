"""Rider lanes on the availability results heatmap.

Each cell used to stack only its own free riders, so the same rider landed on a different
line from one day to the next as the riders above them changed, and could not be followed
along a time slot. A row now gives one lane to every rider free at that time on any day
shown, and every open cell repeats those lanes in the same order: the rider where they are
free, a blank line where they are not.
"""

from datetime import date, timedelta
from types import SimpleNamespace

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
from apps.events.views import _results_row_in_lanes

# --- the rule ----------------------------------------------------------------------------

ORDER = {1: 0, 2: 1, 3: 2}


def _cell(*user_ids: int, blocked: bool = False, race: str = "") -> dict:
    """Build a results cell the way the view does, with just the keys lanes read.

    Args:
        *user_ids: The riders free in the cell.
        blocked: Whether the cell is blocked.
        race: The name of a race scheduled on the cell, if any.

    Returns:
        The cell.

    """
    users = [{"user": SimpleNamespace(pk=user_id)} for user_id in user_ids]
    return {"is_blocked": blocked, "users": users, "selection_name": race}


def _lane_ids(cell: dict) -> list[int | None]:
    """Read a cell's lanes as rider ids, None for a blank line.

    Args:
        cell: A cell after lanes were assigned.

    Returns:
        The rider id in each lane.

    """
    return [entry["user"].pk if entry else None for entry in cell["lanes"]]


def test_every_open_cell_gets_the_same_lanes():
    """The whole change: a rider free on any day keeps one line on every day."""
    row = _results_row_in_lanes("19:00", [_cell(1, 3), _cell(2, 3), _cell(1)], ORDER)

    assert [_lane_ids(cell) for cell in row["cells"]] == [[1, None, 3], [None, 2, 3], [1, None, None]]


def test_lanes_follow_the_lane_order_not_a_cells_own_order():
    """A cell listing its riders differently cannot move them to other lines."""
    row = _results_row_in_lanes("19:00", [_cell(1, 3), _cell(3)], {3: 0, 1: 1})

    assert [_lane_ids(cell) for cell in row["cells"]] == [[3, 1], [3, None]]


def test_a_blocked_cell_has_no_lanes_and_opens_none():
    """A rider only in a blocked cell would otherwise get a lane that is blank everywhere."""
    row = _results_row_in_lanes("19:00", [_cell(1), _cell(1, 2, blocked=True)], ORDER)

    assert _lane_ids(row["cells"][0]) == [1]
    assert row["cells"][1]["lanes"] == []


def test_a_race_reserves_its_line_across_the_row():
    """The race badge takes a line, so every open cell of its row must set one aside."""
    assert _results_row_in_lanes("19:00", [_cell(1), _cell(race="Race 1")], ORDER)["has_race"] is True
    assert _results_row_in_lanes("19:00", [_cell(1), _cell(2)], ORDER)["has_race"] is False


def test_a_race_on_a_blocked_cell_reserves_nothing():
    """The template never draws a badge in a blocked cell, so no line is set aside for one."""
    row = _results_row_in_lanes("19:00", [_cell(1), _cell(blocked=True, race="Race 1")], ORDER)

    assert row["has_race"] is False


def test_a_row_nobody_is_free_in_has_no_lanes():
    """An empty slot stays one short row."""
    row = _results_row_in_lanes("19:00", [_cell(), _cell()], ORDER)

    assert [cell["lanes"] for cell in row["cells"]] == [[], []]


# --- the page ----------------------------------------------------------------------------


@pytest.fixture
def grid(db) -> AvailabilityGrid:
    """Build a published sheet: three days, two one-hour slots, in UTC.

    Returns:
        The grid.

    """
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True)
    squad = Squad.objects.create(event=event, name="Affinity")
    return AvailabilityGrid.objects.create(
        squad=squad,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
        start_time="19:00",
        end_time="21:00",
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


def _grid_rows(client, viewer, grid: AvailabilityGrid) -> list:
    """Render the results page and return the heatmap's rows.

    Args:
        client: Test client.
        viewer: The signed-in user.
        grid: The sheet.

    Returns:
        The heatmap's body rows, parsed.

    """
    client.force_login(viewer)
    response = client.get(
        reverse(
            "events:availability_results",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    )
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser").select("#avail-grid tbody tr")


def _shown(row) -> list[list[str]]:
    """Read what each lane of each day cell in a row shows.

    Args:
        row: A parsed heatmap row.

    Returns:
        Per day, per lane: the rider's name, the race badge, or "" for a blank line.

    """
    days = []
    for td in row.select("td.heatmap-cell"):
        lanes = []
        for lane in td.find_all("div", class_="cell-lane", recursive=False):
            badge = lane.select_one(".slot-badge")
            name = lane.select_one("[data-user-tooltip] > a > span")
            lanes.append((badge or name).get_text(strip=True) if (badge or name) else "")
        days.append(lanes)
    return days


@pytest.mark.django_db
def test_a_rider_keeps_their_line_across_the_days(client, event_admin, user_model, grid):
    """Created out of name order: the lanes still list riders by first name."""
    _rider(user_model, grid, "Cat", ("2026-07-01", "19:00"), ("2026-07-02", "19:00"), ("2026-07-03", "20:00"))
    _rider(user_model, grid, "Ann", ("2026-07-01", "19:00"), ("2026-07-03", "19:00"))
    _rider(user_model, grid, "Ben", ("2026-07-02", "19:00"))

    seven, eight = _grid_rows(client, event_admin, grid)

    assert _shown(seven) == [
        ["Ann Rider", "", "Cat Rider"],
        ["", "Ben Rider", "Cat Rider"],
        ["Ann Rider", "", ""],
    ]
    # Only Cat is free at 20:00, so that row has one lane.
    assert _shown(eight) == [[""], [""], ["Cat Rider"]]


@pytest.mark.django_db
def test_a_scheduled_race_takes_a_line_in_every_open_cell_of_its_row(client, event_admin, user_model, grid):
    """Otherwise the day with the badge sits a line lower than the rest."""
    _rider(user_model, grid, "Ann", ("2026-07-01", "19:00"), ("2026-07-02", "19:00"))
    AvailabilitySlotSelection.objects.create(grid=grid, name="Race 1", slot_date=date(2026, 7, 2), slot_time="19:00")

    seven, eight = _grid_rows(client, event_admin, grid)

    assert _shown(seven) == [["", "Ann Rider"], ["Race 1", "Ann Rider"], ["", ""]]
    assert _shown(eight) == [[], [], []]


@pytest.mark.django_db
def test_a_blocked_cell_draws_no_lanes(client, event_admin, user_model, grid):
    """The stripes fill a blocked cell; the open cells beside it keep their lanes."""
    grid.blocked_cells = [{"date": "2026-07-02", "time": "19:00"}]
    grid.save(update_fields=["blocked_cells"])
    _rider(user_model, grid, "Ann", ("2026-07-01", "19:00"))

    seven, _ = _grid_rows(client, event_admin, grid)

    assert _shown(seven) == [["Ann Rider"], [], [""]]
