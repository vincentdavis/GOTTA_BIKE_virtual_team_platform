"""Chrome around the availability response grid: title, instructions, legend.

All three were showing things that did not apply to the grid in front of the rider.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import AvailabilityGrid, Event, Squad, SquadMember


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL Season 20", start_date=today, end_date=today + timedelta(days=30), visible=True,
    )


def _grid(squad, *, days: int, single=False) -> AvailabilityGrid:
    """Build a published grid spanning `days` days.

    Returns:
        The grid.

    """
    today = date.today()
    return AvailabilityGrid.objects.create(
        squad=squad, start_date=today, end_date=today + timedelta(days=days),
        start_time="18:00", end_time="20:00", slot_duration=60,
        status=AvailabilityGrid.Status.PUBLISHED, single_slot=single,
    )


@pytest.mark.django_db
def test_a_single_day_grid_does_not_repeat_the_date_in_its_title(event) -> None:
    squad = Squad.objects.create(event=event, name="Eclipse")
    today = date.today()

    grid = _grid(squad, days=0, single=True)

    assert grid.title == f"ZRL Season 20 Eclipse {today}"
    assert f"{today} - {today}" not in grid.title


@pytest.mark.django_db
def test_a_multi_day_grid_still_shows_the_span(event) -> None:
    squad = Squad.objects.create(event=event, name="Eclipse")
    today = date.today()

    grid = _grid(squad, days=6)

    assert grid.title == f"ZRL Season 20 Eclipse {today} - {today + timedelta(days=6)}"


@pytest.mark.django_db
def test_an_explicit_title_is_never_overwritten(event) -> None:
    """The auto-title only fills a blank; a captain's own name must survive."""
    squad = Squad.objects.create(event=event, name="Eclipse")
    grid = _grid(squad, days=0)
    grid.title = "Round 3 Qualifier"
    grid.save()

    grid.refresh_from_db()
    assert grid.title == "Round 3 Qualifier"


@pytest.mark.django_db
def test_the_instructions_do_not_call_blocked_cells_dark(client, event, team_member) -> None:
    """Unselected cells are dark too in a dark theme; blocked ones are striped."""
    squad = Squad.objects.create(event=event, name="Eclipse")
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    grid = _grid(squad, days=0, single=True)
    client.force_login(team_member)

    body = client.get(
        reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk])
    ).content.decode()

    assert "Blocked cells (dark)" not in body
    assert "Striped cells cannot be selected" in body


@pytest.mark.django_db
def test_the_conditional_legend_entries_start_hidden(client, event, team_member) -> None:
    """Only buildGrid knows whether the grid has blocked or out-of-timezone cells."""
    squad = Squad.objects.create(event=event, name="Eclipse")
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    grid = _grid(squad, days=0, single=True)
    client.force_login(team_member)

    body = client.get(
        reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk])
    ).content.decode()

    assert 'class="flex items-center gap-1 hidden" data-legend-for="cell-blocked"' in body
    assert 'class="flex items-center gap-1 hidden" data-legend-for="cell-inactive"' in body
    # Available/Unselected always apply, so they are not conditional.
    assert body.count("data-legend-for=") == 2
