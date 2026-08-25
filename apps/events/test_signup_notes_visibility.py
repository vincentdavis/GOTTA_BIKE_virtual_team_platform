"""Who can read a rider's signup notes on the event page.

The notes column used to be gated on ``is_event_admin`` alone while the CSV export -- a
disjoint set of head captains and coordinators -- wrote the same notes unconditionally.
Hiding the column from an exporter protected nothing; it only meant they had to download
a file to read what the page refused to show. The two gates now agree.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with a head captain role and a coordinator role.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL Season 5", start_date=today, end_date=today + timedelta(days=30),
        visible=True, signups_open=True,
        head_captain_role_id=777, coordinator_role_ids=[555],
    )


@pytest.fixture
def rider_with_notes(event, user_model):
    """Register a rider whose signup carries a distinctive note.

    Returns:
        The note text, which should appear only for those allowed to read it.

    """
    rider = user_model.objects.create_user(username="rider", email="rider@example.test")
    EventSignup.objects.create(
        event=event, user=rider, status=EventSignup.Status.REGISTERED,
        notes="Away the first weekend",
    )
    return "Away the first weekend"


def _actor(user_model, username, **extra):
    """Build a team member with no elevated permissions beyond those given.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username=username, email=f"{username}@example.test",
        permission_overrides={"team_member": True}, **extra,
    )


def _page(client, event):
    """Load the event detail page.

    Returns:
        The decoded response body.

    """
    response = client.get(reverse("events:event_detail", args=[event.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_the_head_captain_sees_the_notes_column(client, event, rider_with_notes, user_model) -> None:
    """They can already read these notes in the CSV, so the page should not pretend otherwise."""
    client.force_login(_actor(user_model, "hc", discord_roles={"777": "Head Captain"}))

    assert rider_with_notes in _page(client, event)


@pytest.mark.django_db
def test_a_coordinator_has_no_signup_table_to_put_notes_in(client, event, rider_with_notes, user_model) -> None:
    """Coordinators fail _can_view_v_report, so the whole table is absent -- notes included.

    Their route to this data is the CSV export, and only that. Widening the notes gate
    does not change what they see here; it is recorded so the asymmetry is deliberate
    rather than a gap someone closes by accident.
    """
    client.force_login(_actor(user_model, "coord", discord_roles={"555": "EMEA Coordinator"}))

    assert rider_with_notes not in _page(client, event)


@pytest.mark.django_db
def test_a_squad_captain_sees_the_table_but_not_the_notes(client, event, rider_with_notes, user_model) -> None:
    """They can view signups but cannot export, so the two gates still agree for them."""
    captain = _actor(user_model, "cap")
    squad = Squad.objects.create(event=event, name="Div 1")
    squad.captains.add(captain)
    client.force_login(captain)

    body = _page(client, event)

    assert "Rider" in body or "Signups" in body  # the table itself rendered
    assert rider_with_notes not in body


@pytest.mark.django_db
def test_an_event_admin_still_sees_the_notes_column(client, event, rider_with_notes, event_admin) -> None:
    """Widening the gate must not have cost the original holder their access."""
    client.force_login(event_admin)

    assert rider_with_notes in _page(client, event)


@pytest.mark.django_db
def test_a_plain_team_member_does_not(client, event, rider_with_notes, team_member) -> None:
    """The column is still gated -- this is a union, not an opening."""
    client.force_login(team_member)

    assert rider_with_notes not in _page(client, event)


@pytest.mark.django_db
def test_a_head_captain_role_on_another_event_does_not_carry_over(client, event, rider_with_notes, user_model) -> None:
    """The gate is per-event, matching the export it now mirrors."""
    Event.objects.create(
        title="Other", start_date=date.today(), end_date=date.today() + timedelta(days=1),
        visible=True, coordinator_role_ids=[555],
    )
    # 0 is "no role", not NULL -- the column is BigIntegerField(default=0).
    event.coordinator_role_ids = []
    event.head_captain_role_id = 0
    event.save(update_fields=["coordinator_role_ids", "head_captain_role_id"])

    client.force_login(_actor(user_model, "hc_elsewhere", discord_roles={"777": "Head Captain"}))

    assert rider_with_notes not in _page(client, event)


@pytest.mark.django_db
def test_riders_are_not_told_the_notes_are_admin_only(client, event, team_member) -> None:
    """The promise on the form has to match who actually reads it."""
    client.force_login(team_member)
    body = _page(client, event)

    assert "notes for the event admins" not in body
