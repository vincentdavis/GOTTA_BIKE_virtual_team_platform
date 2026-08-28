"""Numeric availability answers are bounded before they reach the database.

max_races and rest_days are PositiveSmallIntegerField -- Postgres smallint. The view
checked for negatives but had no upper bound, so a mistyped number (reported from a
phone) reached the insert as "smallint out of range" and 500'd the rider's submit, which
they saw as a network error.
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

SMALLINT_MAX = 32767
LIMIT = 365


@pytest.fixture
def grid(db, team_member):
    """Build a published grid that asks both numeric questions.

    Returns:
        The grid, with team_member as a squad member.

    """
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True
    )
    squad = Squad.objects.create(event=event, name="Div 1")
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    return AvailabilityGrid.objects.create(
        squad=squad, start_date=today, end_date=today + timedelta(days=7),
        start_time="18:00", end_time="20:00", slot_duration=60,
        status=AvailabilityGrid.Status.PUBLISHED,
        max_races_question=True, rest_days_question=True,
    )


def _post(client, grid, **answers):
    """Submit an availability response.

    Returns:
        The response.

    """
    body = {"available_cells": [], "max_races": 3, "rest_days": 1}
    body.update(answers)
    return client.post(
        reverse("events:availability_respond",
                args=[grid.squad.event_id, grid.squad_id, str(grid.id)]),
        data=json.dumps(body),
        content_type="application/json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["max_races", "rest_days"])
def test_a_smallint_overflow_is_rejected_not_a_500(auth_client, grid, field) -> None:
    """The exact production failure: smallint out of range on insert."""
    response = _post(auth_client, grid, **{field: SMALLINT_MAX + 1})

    assert response.status_code == 400
    assert "between 0 and" in response.json()["error"]
    assert not AvailabilityResponse.objects.filter(grid=grid).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["max_races", "rest_days"])
def test_anything_over_a_year_is_rejected(auth_client, grid, field) -> None:
    """Both answers are counts bounded by a year; beyond that it is a typo."""
    response = _post(auth_client, grid, **{field: LIMIT + 1})

    assert response.status_code == 400
    assert not AvailabilityResponse.objects.filter(grid=grid).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["max_races", "rest_days"])
def test_the_limit_itself_is_accepted(auth_client, grid, field) -> None:
    """An off-by-one here would reject a legitimate answer."""
    assert _post(auth_client, grid, **{field: LIMIT}).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["max_races", "rest_days"])
def test_a_negative_answer_is_still_rejected(auth_client, grid, field) -> None:
    """The lower bound was already there and must survive the rewrite."""
    response = _post(auth_client, grid, **{field: -1})

    assert response.status_code == 400


@pytest.mark.django_db
def test_a_reasonable_answer_still_saves(auth_client, grid, team_member) -> None:
    """The bound must not have cost the normal case."""
    response = _post(auth_client, grid, max_races=4, rest_days=2)

    assert response.status_code == 200
    saved = AvailabilityResponse.objects.get(grid=grid, user=team_member)
    assert (saved.max_races, saved.rest_days) == (4, 2)


@pytest.mark.django_db
def test_a_non_numeric_answer_is_rejected(auth_client, grid) -> None:
    """Unchanged behaviour, kept under test through the refactor."""
    assert _post(auth_client, grid, max_races="lots").status_code == 400


@pytest.mark.django_db
def test_a_blank_required_answer_is_still_asked_for(auth_client, grid) -> None:
    """The prompt wording differs per field, so both paths matter."""
    response = _post(auth_client, grid, rest_days="")

    assert response.status_code == 400
    assert "rest days between races" in response.json()["error"]
