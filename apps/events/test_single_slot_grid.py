"""Single-slot availability grids.

A single-slot grid is an ordinary grid whose time axis has one row: end_date equals
start_date and end_time is start_time plus the slot duration. Nothing downstream --
responses, the v-report, scheduled races -- sees a new shape, so these tests are about
the derivation producing exactly one cell, including when it wraps past midnight.
"""

import json
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import AvailabilityGrid, Event, Squad
from apps.events.tz_utils import convert_grid_to_local


@pytest.fixture
def squad(db) -> Squad:
    """Build a squad on a visible event.

    Returns:
        The squad.

    """
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True,
    )
    return Squad.objects.create(event=event, name="Synthesis")


def _post(client, squad, **overrides):
    """Save a grid through the builder endpoint.

    Returns:
        The response.

    """
    payload = {
        "start_date": "2026-09-01",
        "start_time": "19:00",
        "slot_duration": 60,
        "timezone": "UTC",
        "blocked_cells": [],
        "single_slot": True,
        **overrides,
    }
    return client.post(
        reverse("events:availability_create", args=[squad.event.pk, squad.pk]),
        data=json.dumps(payload),
        content_type="application/json",
    )


def _cells(grid: AvailabilityGrid) -> list[str]:
    """Expand a grid into its display cells.

    Returns:
        Sorted "date|time" keys.

    """
    data = convert_grid_to_local(
        [d.isoformat() for d in _dates(grid)],
        grid.start_time,
        grid.end_time,
        grid.slot_duration,
        grid.blocked_cells,
        grid.grid_timezone,
    )
    return sorted(data["valid_cells"])


def _dates(grid: AvailabilityGrid) -> list[date]:
    """Every UTC date the grid spans.

    Returns:
        A list of dates.

    """
    span = (grid.end_date - grid.start_date).days
    return [grid.start_date + timedelta(days=i) for i in range(span + 1)]


@pytest.mark.django_db
def test_a_single_slot_grid_has_exactly_one_cell(client, squad, event_admin) -> None:
    client.force_login(event_admin)

    resp = _post(client, squad)

    assert resp.status_code in (200, 201), resp.content
    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.single_slot is True
    assert grid.start_date == grid.end_date
    assert grid.start_time == "19:00"
    assert grid.end_time == "20:00"
    assert _cells(grid) == ["2026-09-01|19:00"]


@pytest.mark.django_db
def test_a_late_start_wraps_midnight_and_is_still_one_cell(client, squad, event_admin) -> None:
    """23:30 + 60min ends at 00:30, which the generator treats as the next day."""
    client.force_login(event_admin)

    resp = _post(client, squad, start_time="23:30")

    assert resp.status_code in (200, 201), resp.content
    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.end_time == "00:30"
    assert _cells(grid) == ["2026-09-01|23:30"]


@pytest.mark.django_db
def test_minute_precision_is_kept(client, squad, event_admin) -> None:
    client.force_login(event_admin)

    _post(client, squad, start_time="19:07", slot_duration=15)

    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.start_time == "19:07"
    assert grid.end_time == "19:22"
    assert _cells(grid) == ["2026-09-01|19:07"]


@pytest.mark.django_db
def test_an_end_date_from_the_client_is_ignored(client, squad, event_admin) -> None:
    """The server decides the span; a stray end_date must not widen a single-slot grid."""
    client.force_login(event_admin)

    _post(client, squad, end_date="2026-09-30")

    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.end_date == grid.start_date
    assert len(_cells(grid)) == 1


@pytest.mark.django_db
def test_an_ordinary_grid_is_unaffected(client, squad, event_admin) -> None:
    """The normal path still requires an end time and still rejects an inverted range."""
    client.force_login(event_admin)

    ok = _post(client, squad, single_slot=False, end_date="2026-09-02", end_time="21:00")
    assert ok.status_code in (200, 201), ok.content
    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.single_slot is False
    assert len(_cells(grid)) == 4  # 2 days x 2 hourly slots

    grid.delete()
    bad = _post(client, squad, single_slot=False, end_date="2026-09-02", end_time="18:00")
    assert bad.status_code == 400
    assert b"before end_time" in bad.content


@pytest.mark.django_db
def test_the_respond_page_shows_a_yes_no_and_names_the_slot(client, squad, user_model) -> None:
    """A single Yes/No replaces the grid, so the date and time have to be stated."""
    from apps.events.models import SquadMember

    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test",
        permission_overrides={"team_member": True},
    )
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    grid = AvailabilityGrid.objects.create(
        squad=squad, single_slot=True, status=AvailabilityGrid.Status.PUBLISHED,
        start_date=date(2026, 9, 1), end_date=date(2026, 9, 1),
        start_time="19:00", end_time="20:00", slot_duration=60, grid_timezone="UTC",
    )
    client.force_login(rider)

    body = client.get(
        reverse("events:availability_respond", args=[squad.event.pk, squad.pk, grid.pk])
    ).content.decode()

    assert 'id="single-slot-answer"' in body
    assert "I am available" in body
    assert "Tue 01 Sep 2026 at 19:00" in body
    assert "60 min" in body


@pytest.mark.django_db
def test_an_ordinary_grid_gets_no_yes_no_block(client, squad, user_model) -> None:
    from apps.events.models import SquadMember

    rider = user_model.objects.create_user(
        username="rider2", email="rider2@example.test",
        permission_overrides={"team_member": True},
    )
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    grid = AvailabilityGrid.objects.create(
        squad=squad, single_slot=False, status=AvailabilityGrid.Status.PUBLISHED,
        start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
        start_time="19:00", end_time="21:00", slot_duration=60, grid_timezone="UTC",
    )
    client.force_login(rider)

    body = client.get(
        reverse("events:availability_respond", args=[squad.event.pk, squad.pk, grid.pk])
    ).content.decode()

    assert 'id="single-slot-answer"' not in body
