"""The "View Availability" columns on the participation report.

A rider who submitted a response with nothing marked said "no" -- on a single-slot grid
that is literally the No button. That is a different answer from never having responded,
so the two states have to stay distinguishable in the report.
"""

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


def _grid(
    squad, *, days_from_today: int, starts_in: int = 0,
    status=AvailabilityGrid.Status.PUBLISHED, single=False,
):
    """Build a grid opening in `starts_in` days and ending `days_from_today` days out.

    Returns:
        The grid.

    """
    today = date.today()
    return AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today + timedelta(days=starts_in),
        end_date=today + timedelta(days=days_from_today),
        start_time="18:00",
        end_time="20:00",
        slot_duration=60,
        status=status,
        single_slot=single,
    )


def _rider(user_model, squad, username):
    """Add a squad member.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(username=username, email=f"{username}@example.test")
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _row(resp, squad, user):
    """Pull one rider's participation row out of the response context.

    Returns:
        The row dict.

    """
    group = next(g for g in resp.context["participation"] if g["squad"].pk == squad.pk)
    return next(r for r in group["rows"] if r["user"].pk == user.pk)


@pytest.mark.django_db
def test_the_three_answer_states_are_distinguished(client, event, event_admin, user_model) -> None:
    """Marked slots, an empty response, and silence are three different answers."""
    squad = Squad.objects.create(event=event, name="Synthesis")
    grid = _grid(squad, days_from_today=7)
    yes = _rider(user_model, squad, "rider_yes")
    no = _rider(user_model, squad, "rider_no")
    silent = _rider(user_model, squad, "rider_silent")

    AvailabilityResponse.objects.create(
        grid=grid, user=yes,
        available_cells=[{"date": date.today().isoformat(), "time": "18:00"}],
    )
    # Submitted, nothing marked -- a deliberate "not available", not a missing answer.
    AvailabilityResponse.objects.create(grid=grid, user=no, available_cells=[])

    client.force_login(event_admin)
    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    assert _row(resp, squad, yes)["availability"][0]["state"] == "yes"
    assert _row(resp, squad, no)["availability"][0]["state"] == "no"
    assert _row(resp, squad, silent)["availability"][0]["state"] == "none"


@pytest.mark.django_db
def test_drafts_get_no_column_but_finished_sheets_do(client, event, event_admin, user_model) -> None:
    """Captains close a sheet once they have picked, so "still open" empties most squads.

    Restricting the columns to sheets still open for responses left every squad that
    runs week by week with no columns at all. A draft is different: nobody was ever
    asked, so there is no answer to show.
    """
    squad = Squad.objects.create(event=event, name="Synthesis")
    _rider(user_model, squad, "rider_a")
    live = _grid(squad, days_from_today=7)
    _grid(squad, days_from_today=7, status=AvailabilityGrid.Status.DRAFT)
    closed = _grid(squad, days_from_today=7, status=AvailabilityGrid.Status.CLOSED)
    # Published but its window ended yesterday.
    ended = _grid(squad, days_from_today=7)
    ended.end_date = date.today() - timedelta(days=1)
    ended.save(update_fields=["end_date"])

    client.force_login(event_admin)
    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    group = next(g for g in resp.context["participation"] if g["squad"].pk == squad.pk)
    assert {c["grid"].pk for c in group["grids"]} == {live.pk, closed.pk, ended.pk}
    # Only the one still taking responses is flagged open; the header mutes the rest.
    assert {c["grid"].pk: c["is_open"] for c in group["grids"]} == {
        live.pk: True, closed.pk: False, ended.pk: False,
    }
    assert resp.context["has_open_grids"] is True


@pytest.mark.django_db
def test_every_squad_gets_its_own_columns(client, event, event_admin, user_model) -> None:
    """Squads run their own sheets, on their own dates, in their own number."""
    one = Squad.objects.create(event=event, name="Amnesia")
    two = Squad.objects.create(event=event, name="Annihilation")
    _rider(user_model, one, "rider_one")
    _rider(user_model, two, "rider_two")
    one_a = _grid(one, days_from_today=7)
    one_b = _grid(one, starts_in=8, days_from_today=14)
    two_a = _grid(two, days_from_today=3, status=AvailabilityGrid.Status.CLOSED)

    client.force_login(event_admin)
    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    by_squad = {g["squad"].pk: [c["grid"].pk for c in g["grids"]] for g in resp.context["participation"]}
    assert by_squad[one.pk] == [one_a.pk, one_b.pk]
    assert by_squad[two.pk] == [two_a.pk]


@pytest.mark.django_db
def test_the_toggle_is_hidden_when_nothing_is_open(client, event, event_admin, user_model) -> None:
    """Nothing to toggle, so the control would only be confusing."""
    squad = Squad.objects.create(event=event, name="Synthesis")
    _rider(user_model, squad, "rider_a")
    client.force_login(event_admin)

    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    assert resp.context["has_open_grids"] is False
    assert 'id="toggle-availability"' not in resp.content.decode()


@pytest.mark.django_db
def test_the_columns_render_behind_the_toggle(client, event, event_admin, user_model) -> None:
    """The toggle is client-side, so the cells are in the markup either way."""
    squad = Squad.objects.create(event=event, name="Synthesis")
    rider = _rider(user_model, squad, "rider_a")
    grid = _grid(squad, days_from_today=7)
    AvailabilityResponse.objects.create(grid=grid, user=rider, available_cells=[])

    client.force_login(event_admin)
    body = client.get(
        reverse("events:event_all_races", args=[event.pk]) + "?tab=participation"
    ).content.decode()

    assert 'id="toggle-availability"' in body
    assert "View Availability" in body
    assert 'data-col="availability"' in body
    assert "Responded: not available" in body


@pytest.mark.django_db
def test_a_single_slot_grid_reads_as_a_plain_yes(client, event, event_admin, user_model) -> None:
    """There is only one slot, so "3 slots marked available" would be nonsense."""
    squad = Squad.objects.create(event=event, name="Synthesis")
    rider = _rider(user_model, squad, "rider_a")
    grid = _grid(squad, days_from_today=0, single=True)
    AvailabilityResponse.objects.create(
        grid=grid, user=rider,
        available_cells=[{"date": date.today().isoformat(), "time": "18:00"}],
    )

    client.force_login(event_admin)
    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    assert _row(resp, squad, rider)["availability"][0]["detail"] == "Available"
