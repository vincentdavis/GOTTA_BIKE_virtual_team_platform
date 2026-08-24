"""Editing an availability sheet after it is published.

A response stores UTC "date|time" strings with no foreign key to a cell, and a rider's
next save is a wholesale replace -- so a shape change silently orphans answers and the
rider's next submit deletes them. Everything that decides which cells exist is therefore
frozen once anyone has answered; everything else stays editable.
"""

import json
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    Squad,
    SquadMember,
)


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True,
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad on the event.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Eclipse")


@pytest.fixture
def grid(squad) -> AvailabilityGrid:
    """Build a published sheet spanning a week.

    Returns:
        The grid.

    """
    return AvailabilityGrid.objects.create(
        squad=squad, start_date=date(2026, 7, 1), end_date=date(2026, 7, 7),
        start_time="19:00", end_time="21:00", slot_duration=60, grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _payload(**over) -> dict:
    """Build a builder POST body matching the `grid` fixture.

    Returns:
        The payload dict.

    """
    body = {
        "title": "", "start_date": "2026-07-01", "end_date": "2026-07-07",
        "start_time": "19:00", "end_time": "21:00", "slot_duration": 60,
        "timezone": "UTC", "blocked_cells": [], "expires": "",
        "max_races_question": False, "rest_days_question": False,
    }
    body.update(over)
    return body


def _post(client, event, squad, grid, **over):
    """POST an edit to the builder.

    Returns:
        The response.

    """
    return client.post(
        reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]),
        data=json.dumps(_payload(**over)), content_type="application/json",
    )


def _respond(grid, user, cells=None) -> AvailabilityResponse:
    """Store a rider's answer.

    Returns:
        The response row.

    """
    return AvailabilityResponse.objects.create(
        grid=grid, user=user,
        available_cells=cells if cells is not None else [{"date": "2026-07-03", "time": "19:00"}],
    )


@pytest.mark.django_db
def test_a_published_sheet_with_no_responses_can_still_be_reshaped(client, event, squad, grid, event_admin) -> None:
    """Nothing to protect yet, so publishing alone must not freeze the sheet."""
    client.force_login(event_admin)

    resp = _post(client, event, squad, grid, end_date="2026-07-03")

    assert resp.status_code == 200
    grid.refresh_from_db()
    assert grid.end_date == date(2026, 7, 3)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_date", "2026-07-02"),
        ("end_date", "2026-07-03"),
        ("start_time", "20:00"),
        ("end_time", "22:00"),
        ("slot_duration", 30),
        ("timezone", "Europe/London"),
        ("blocked_cells", [{"date": "2026-07-03", "time": "19:00"}]),
    ],
)
def test_every_shape_field_is_refused_once_a_rider_has_answered(
    client, event, squad, grid, event_admin, team_member, field, value
) -> None:
    _respond(grid, team_member)
    client.force_login(event_admin)
    before = AvailabilityGrid.objects.get(pk=grid.pk)

    resp = _post(client, event, squad, grid, **{field: value})

    assert resp.status_code == 400
    assert "already has responses" in resp.json()["error"]
    after = AvailabilityGrid.objects.get(pk=grid.pk)
    for name in ("start_date", "end_date", "start_time", "end_time",
                 "slot_duration", "grid_timezone", "blocked_cells"):
        assert getattr(after, name) == getattr(before, name), name


@pytest.mark.django_db
def test_the_timezone_alone_is_refused(client, event, squad, grid, event_admin, team_member) -> None:
    """The sneakiest edit: the wall-clock numbers never move, but every UTC cell does."""
    _respond(grid, team_member)
    client.force_login(event_admin)

    resp = _post(client, event, squad, grid, timezone="Europe/London")

    assert resp.status_code == 400
    assert "timezone" in resp.json()["error"]
    grid.refresh_from_db()
    assert grid.grid_timezone == "UTC"


@pytest.mark.django_db
def test_the_editable_settings_still_save_with_responses_present(
    client, event, squad, grid, event_admin, team_member
) -> None:
    """The point of the change: a captain can fix wording without touching the shape."""
    _respond(grid, team_member)
    client.force_login(event_admin)

    resp = _post(
        client, event, squad, grid,
        title="Round 3 Qualifier", max_races_question=True, rest_days_question=True,
        require_race_verified_availability=True, expanded_features=True,
        description="Bring a spare wheel.", recon_url="https://example.test/recon",
    )

    assert resp.status_code == 200
    grid.refresh_from_db()
    assert grid.title == "Round 3 Qualifier"
    assert grid.max_races_question is True
    assert grid.require_race_verified_availability is True
    assert grid.description == "Bring a spare wheel."


@pytest.mark.django_db
def test_reordered_blocked_cells_are_not_treated_as_a_change(
    client, event, squad, grid, event_admin, team_member
) -> None:
    """It is a JSON list whose order carries no meaning; a re-save must not trip the guard."""
    grid.blocked_cells = [
        {"date": "2026-07-03", "time": "19:00"},
        {"date": "2026-07-04", "time": "20:00"},
    ]
    grid.save(update_fields=["blocked_cells"])
    _respond(grid, team_member)
    client.force_login(event_admin)

    resp = _post(client, event, squad, grid, blocked_cells=[
        {"date": "2026-07-04", "time": "20:00"},
        {"date": "2026-07-03", "time": "19:00"},
    ])

    assert resp.status_code == 200


@pytest.mark.django_db
def test_a_no_op_resave_of_an_answered_sheet_is_accepted(
    client, event, squad, grid, event_admin, team_member
) -> None:
    """Opening the builder and pressing Save must not be an error."""
    _respond(grid, team_member)
    client.force_login(event_admin)

    assert _post(client, event, squad, grid).status_code == 200


@pytest.mark.django_db
def test_the_builder_reports_who_would_be_locked_out_by_race_verified(
    client, event, squad, grid, event_admin, user_model
) -> None:
    """The captain should see the cost of ticking the box before they tick it."""
    verified = user_model.objects.create_user(
        username="v", email="v@example.test", is_race_ready=True)
    unverified = user_model.objects.create_user(username="u", email="u@example.test")
    for user in (verified, unverified):
        SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
        _respond(grid, user)
    client.force_login(event_admin)

    resp = client.get(reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]))

    assert resp.context["response_count"] == 2
    assert resp.context["unverified_responders"] == 1
    assert 'id="race-verified-lockout-note"' in resp.content.decode()


@pytest.mark.django_db
def test_the_lockout_count_is_skipped_when_the_rule_is_already_on(
    client, event, squad, grid, event_admin, user_model
) -> None:
    """Nobody is newly locked out by a box that was already ticked."""
    grid.require_race_verified_availability = True
    grid.save(update_fields=["require_race_verified_availability"])
    user = user_model.objects.create_user(username="u", email="u@example.test")
    _respond(grid, user)
    client.force_login(event_admin)

    resp = client.get(reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]))

    assert resp.context["unverified_responders"] == 0
