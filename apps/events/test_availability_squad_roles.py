"""The squad captain and vice-captain marks on an availability sheet's results.

Wherever a results page names a rider -- the heatmap, the responders and waiting lists, the
scheduled races, a series sheet's rider column and the race dialog -- a captain gets 🎖️ and a
vice-captain 🏅, the icons the squad panel already uses, with the role as the tooltip.
"""

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse

from apps.events import series
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    AvailabilitySlotSelection,
    Event,
    SeriesStage,
    SheetStage,
    Squad,
    SquadMember,
)


@pytest.fixture
def squad(db) -> Squad:
    """Build a squad in a visible weekly event.

    Returns:
        The squad.

    """
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True)
    return Squad.objects.create(event=event, name="Affinity")


def _grid(squad: Squad) -> AvailabilityGrid:
    """Publish a one-day sheet with two one-hour slots, in UTC.

    Returns:
        The grid.

    """
    return AvailabilityGrid.objects.create(
        squad=squad,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        start_time="19:00",
        end_time="21:00",
        slot_duration=60,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _member(user_model, squad: Squad, name: str):
    """Add a squad member.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=name.lower(), email=f"{name.lower()}@example.test", first_name=name, last_name="Rider"
    )
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _free(grid: AvailabilityGrid, user) -> None:
    """Mark the rider free in the sheet's first slot."""
    AvailabilityResponse.objects.create(grid=grid, user=user, available_cells=[{"date": "2026-07-01", "time": "19:00"}])


def _page(client, viewer, grid: AvailabilityGrid) -> BeautifulSoup:
    """Render the results page.

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


def _marks(scope) -> dict[str, str]:
    """Read the role mark beside each rider name in part of the page.

    The mark sits inside the name, on its line; in a heatmap lane, whose name is cut short
    to fit, it sits just before the rider link instead, like the race flag.

    Returns:
        ``{rider name: "Captain" | "Vice Captain" | ""}``, for every rider named there.

    """
    marks = {}
    for tooltip in scope.select("[data-user-tooltip]"):
        name_span = tooltip.select_one("a > span")
        name = "".join(name_span.find_all(string=True, recursive=False)).strip()
        mark = name_span.select_one(".squad-role") or tooltip.find_previous_sibling(class_="squad-role")
        marks[name] = mark["aria-label"] if mark else ""
    return marks


@pytest.mark.django_db
def test_captains_and_vice_captains_are_marked_wherever_the_weekly_results_name_them(
    client, event_admin, user_model, squad
):
    """Heatmap, responders, waiting list and a scheduled race; everyone else is left plain."""
    grid = _grid(squad)
    cap, vice, rider = (_member(user_model, squad, name) for name in ("Cap", "Vice", "Plain"))
    waiting_vice = _member(user_model, squad, "Late")
    squad.captains.add(cap)
    squad.vice_captains.add(vice, waiting_vice)
    for user in (cap, vice, rider):
        _free(grid, user)
    race = AvailabilitySlotSelection.objects.create(
        grid=grid, name="Race 1", slot_date=date(2026, 7, 1), slot_time="20:00"
    )
    race.selected_users.add(cap, rider)

    page = _page(client, event_admin, grid)

    expected = {"Cap Rider": "Captain", "Vice Rider": "Vice Captain", "Plain Rider": ""}
    assert _marks(page.select_one("#avail-grid")) == expected
    assert _marks(page.select_one("[data-responders-table]")) == expected
    waiting = page.find("h3", string=lambda text: text and text.startswith("Waiting Response")).parent
    assert _marks(waiting) == {"Late Rider": "Vice Captain"}
    assert _marks(page.select_one("#slot-selections-container")) == {"Cap Rider": "Captain", "Plain Rider": ""}
    mark = page.select_one("[data-responders-table] .squad-role")
    assert (mark.get_text(), mark["title"]) == ("🎖️", "Captain")
    assert "Vice Captain" in page.text  # the legend explains both marks


@pytest.mark.django_db
def test_a_rider_who_is_both_reads_as_captain(client, event_admin, user_model, squad):
    """Listed as captain and vice-captain, a rider wears the captain's mark alone."""
    grid = _grid(squad)
    both = _member(user_model, squad, "Both")
    squad.captains.add(both)
    squad.vice_captains.add(both)
    _free(grid, both)

    page = _page(client, event_admin, grid)

    assert _marks(page.select_one("[data-responders-table]")) == {"Both Rider": "Captain"}


@pytest.mark.django_db
def test_a_squad_without_leaders_shows_no_marks_and_no_legend(client, event_admin, user_model, squad):
    """The legend line appears only when some rider on the page wears a mark."""
    grid = _grid(squad)
    _free(grid, _member(user_model, squad, "Plain"))

    page = _page(client, event_admin, grid)

    assert not page.select(".squad-role")
    assert "Vice Captain" not in page.text


@pytest.mark.django_db
def test_the_race_dialog_knows_each_riders_role(client, event_admin, user_model, squad):
    """The dialog draws its rider list from user-data, so the role travels with it."""
    grid = _grid(squad)
    cap = _member(user_model, squad, "Cap")
    rider = _member(user_model, squad, "Plain")
    squad.captains.add(cap)
    _free(grid, cap)
    _free(grid, rider)

    page = _page(client, event_admin, grid)

    data = json.loads(page.select_one("#user-data").string)
    assert (data[str(cap.pk)]["squad_role"], data[str(rider.pk)]["squad_role"]) == ("Captain", "")


@pytest.mark.django_db
def test_a_series_sheet_marks_its_leaders_in_the_rider_column(client, event_admin, user_model):
    """A race series' results list riders by stage; the leaders are marked there too."""
    today = date.today()
    event = Event.objects.create(
        title="Winter Series",
        start_date=today,
        end_date=today + timedelta(days=60),
        visible=True,
        availability_type=Event.AvailabilityType.SERIES,
    )
    stage = SeriesStage.objects.create(event=event, order=0, name="Opener", stage_type="points", riders_min=2)
    squad = Squad.objects.create(event=event, name="Caliber", squad_timezone="UTC")
    grid = AvailabilityGrid(
        squad=squad, kind=AvailabilityGrid.Kind.SERIES, status=AvailabilityGrid.Status.PUBLISHED, grid_timezone="UTC"
    )
    starts = datetime.combine(today + timedelta(days=10), time(19, 30), tzinfo=UTC)
    rows = [SheetStage(stage=stage, starts_at=starts, racing=True)]
    series.sync_sheet_span(grid, rows)
    grid.save()
    for row in rows:
        row.grid = grid
        row.save()
    cap, vice, _plain = (_member(user_model, squad, name) for name in ("Cap", "Vice", "Plain"))
    squad.captains.add(cap)
    squad.vice_captains.add(vice)

    page = _page(client, event_admin, grid)

    rider_column = page.select_one("table tbody")
    assert _marks(rider_column) == {"Cap Rider": "Captain", "Plain Rider": "", "Vice Rider": "Vice Captain"}
