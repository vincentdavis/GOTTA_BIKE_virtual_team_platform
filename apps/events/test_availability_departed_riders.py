"""A rider who answers an availability sheet and then leaves the squad.

Their response is keyed on (grid, user) with no link to the membership, so leaving does not
remove it -- and it should not be removed: it is the rider's own answer. What has to stop is
the answer COUNTING. Before this, a departed rider still appeared as a responder, still
added to the heatmap, was still offered in the race-slot picker (the path by which someone
outside the squad ends up on a race roster), and still held the sheet's shape lock.

All of those now read AvailabilityGrid.active_responses(). The participation report was
already correct -- it builds rows from members and looks answers up -- and is not tested here.
"""

import json
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import AvailabilityGrid, AvailabilityResponse, Event, Squad, SquadMember

CELL = {"date": "2026-07-03", "time": "19:00"}


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Affinity")


@pytest.fixture
def grid(squad) -> AvailabilityGrid:
    """Build a published sheet.

    Returns:
        The grid.

    """
    return AvailabilityGrid.objects.create(
        squad=squad, start_date=date(2026, 7, 1), end_date=date(2026, 7, 7),
        start_time="19:00", end_time="21:00", slot_duration=60, grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _rider(user_model, squad, name: str, *, status=SquadMember.Status.MEMBER):
    """Add a rider to the squad.

    Args:
        user_model: The active user model.
        squad: The squad to join.
        name: First name, also the username.
        status: Membership status.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=name.lower(), email=f"{name.lower()}@example.test", first_name=name, last_name="Rider"
    )
    SquadMember.objects.create(squad=squad, user=user, status=status)
    return user


def _answer(grid, user) -> AvailabilityResponse:
    """Record a rider marking one cell available.

    Args:
        grid: The sheet.
        user: The rider.

    Returns:
        The response.

    """
    return AvailabilityResponse.objects.create(grid=grid, user=user, available_cells=[CELL])


def _leave(squad, user) -> None:
    """Remove a rider from the squad, the way every leave path in the app does it.

    Args:
        squad: The squad being left.
        user: The rider leaving.

    """
    SquadMember.objects.filter(squad=squad, user=user).delete()


# --- the definition --------------------------------------------------------------------


@pytest.mark.django_db
def test_a_departed_riders_response_stops_counting(user_model, squad, grid):
    """The whole change, at the model: gone from the squad, gone from the count."""
    stays = _rider(user_model, squad, "Stays")
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, stays)
    _answer(grid, leaves)
    _leave(squad, leaves)

    assert list(grid.active_responses().values_list("user__username", flat=True)) == ["stays"]


@pytest.mark.django_db
def test_the_response_itself_is_kept(user_model, squad, grid):
    """Non-destructive: the answer is the rider's, and hiding it must not delete it."""
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, leaves)
    _leave(squad, leaves)

    assert AvailabilityResponse.objects.filter(grid=grid, user=leaves).exists()


@pytest.mark.django_db
def test_a_pending_applicant_does_not_count(user_model, squad, grid):
    """Not yet a squad-mate, so not yet a responder -- same rule as the participation report."""
    pending = _rider(user_model, squad, "Pending", status=SquadMember.Status.PENDING)
    _answer(grid, pending)

    assert not grid.active_responses().exists()


@pytest.mark.django_db
def test_membership_of_another_squad_does_not_leak_in(user_model, event, squad, grid):
    """Both conditions must match the SAME membership row.

    Split across two filter() calls, Django may satisfy "is in this squad" and "is a MEMBER"
    from two different rows -- so a rider who is a full member elsewhere but only pending
    here would count. This is the case that would catch that regression.
    """
    other = Squad.objects.create(event=event, name="Other")
    rider = _rider(user_model, squad, "Split", status=SquadMember.Status.PENDING)
    SquadMember.objects.create(squad=other, user=rider, status=SquadMember.Status.MEMBER)
    _answer(grid, rider)

    assert not grid.active_responses().exists()


@pytest.mark.django_db
def test_the_count_excludes_departed_riders(user_model, squad, grid):
    """Shown on the sheet list, the menu badge and the manage page."""
    _answer(grid, _rider(user_model, squad, "Stays"))
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, leaves)
    _leave(squad, leaves)

    assert grid.response_count == 1


# --- the results page: list, heatmap and race-slot picker -------------------------------


def _results(client, viewer, grid):
    """Render the results page.

    Args:
        client: Test client.
        viewer: The signed-in user.
        grid: The sheet.

    Returns:
        The response.

    """
    client.force_login(viewer)
    return client.get(
        reverse(
            "events:availability_results",
            kwargs={"event_pk": grid.squad.event_id, "squad_pk": grid.squad_id, "grid_pk": grid.pk},
        )
    )


@pytest.mark.django_db
def test_a_departed_rider_is_not_listed_as_a_responder(client, event_admin, user_model, squad, grid):
    """The reported bug: they left, and were still listed on the sheet."""
    _answer(grid, _rider(user_model, squad, "Stays"))
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, leaves)
    _leave(squad, leaves)

    response = _results(client, event_admin, grid)

    responders = [entry["user"].username for entry in response.context["enriched_responders"]]
    assert responders == ["stays"]
    assert response.context["total_responders"] == 1


@pytest.mark.django_db
def test_a_departed_rider_is_not_offered_for_a_race_slot(client, event_admin, user_model, squad, grid):
    """The consequential one: the slot picker is how a rider ends up on a race roster.

    The heatmap and the picker are both fed from utc_cell_users_json, so a departed rider
    absent from it can neither inflate "N available" nor be selected for the race.
    """
    stays = _rider(user_model, squad, "Stays")
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, stays)
    _answer(grid, leaves)
    _leave(squad, leaves)

    cell_users = _results(client, event_admin, grid).context["utc_cell_users_json"]

    assert cell_users[f"{CELL['date']}|{CELL['time']}"] == [stays.pk]


# --- the shape lock --------------------------------------------------------------------


def _reshape(client, viewer, grid, **over):
    """POST a builder edit changing the sheet's shape.

    Args:
        client: Test client.
        viewer: The signed-in user.
        grid: The sheet.
        **over: Fields to change.

    Returns:
        The response.

    """
    body = {
        "title": "", "start_date": "2026-07-01", "end_date": "2026-07-07",
        "start_time": "19:00", "end_time": "21:00", "slot_duration": 60,
        "timezone": "UTC", "blocked_cells": [], "expires": "",
        "max_races_question": False, "rest_days_question": False,
    }
    body.update(over)
    client.force_login(viewer)
    return client.post(
        reverse("events:availability_edit", args=[grid.squad.event_id, grid.squad_id, grid.id]),
        data=json.dumps(body), content_type="application/json",
    )


@pytest.mark.django_db
def test_answers_only_from_departed_riders_no_longer_freeze_the_sheet(client, event_admin, user_model, squad, grid):
    """Nobody in the squad has answered, so there is nothing to protect."""
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, leaves)
    _leave(squad, leaves)

    response = _reshape(client, event_admin, grid, end_date="2026-07-03")

    assert response.status_code == 200
    grid.refresh_from_db()
    assert grid.end_date == date(2026, 7, 3)


@pytest.mark.django_db
def test_one_current_answer_still_freezes_it(client, event_admin, user_model, squad, grid):
    """The lock is not weakened -- only answers that no longer matter stop counting."""
    _answer(grid, _rider(user_model, squad, "Stays"))
    leaves = _rider(user_model, squad, "Leaves")
    _answer(grid, leaves)
    _leave(squad, leaves)

    response = _reshape(client, event_admin, grid, end_date="2026-07-03")

    assert response.status_code == 400
    assert "already has responses" in response.json()["error"]
