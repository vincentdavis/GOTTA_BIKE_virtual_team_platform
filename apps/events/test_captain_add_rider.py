"""Squad captains can build their own roster; the add-rider picker groups by current squad."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with signups open.

    Returns:
        The event under test.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True, signups_open=True
    )


def _member(user_model, username: str):
    """Create a team member.

    Args:
        user_model: The active user model.
        username: Username to create.

    Returns:
        The created user.

    """
    return user_model.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        first_name=username.title(),
        last_name="R",
        permission_overrides={"team_member": True},
    )


@pytest.mark.django_db
def test_captain_can_add_a_rider_to_their_own_squad(client, event, user_model) -> None:
    captain = _member(user_model, "cap")
    rider = _member(user_model, "rider")
    squad = Squad.objects.create(event=event, name="Alpha")
    squad.captains.add(captain)
    signup = EventSignup.objects.create(event=event, user=rider)

    client.force_login(captain)
    client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
    )

    assert SquadMember.objects.filter(squad=squad, user=rider).exists()


@pytest.mark.django_db
def test_captain_cannot_add_to_a_squad_they_do_not_lead(client, event, user_model) -> None:
    captain = _member(user_model, "cap")
    rider = _member(user_model, "rider")
    mine = Squad.objects.create(event=event, name="Alpha")
    mine.captains.add(captain)
    theirs = Squad.objects.create(event=event, name="Bravo")
    signup = EventSignup.objects.create(event=event, user=rider)

    client.force_login(captain)
    client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": theirs.pk},
    )

    assert not SquadMember.objects.filter(squad=theirs, user=rider).exists()


@pytest.mark.django_db
def test_captain_can_remove_from_their_own_squad_only(client, event, user_model) -> None:
    captain = _member(user_model, "cap")
    rider = _member(user_model, "rider")
    mine = Squad.objects.create(event=event, name="Alpha")
    mine.captains.add(captain)
    theirs = Squad.objects.create(event=event, name="Bravo")
    signup = EventSignup.objects.create(event=event, user=rider)
    SquadMember.objects.create(squad=mine, user=rider, status=SquadMember.Status.MEMBER)
    SquadMember.objects.create(squad=theirs, user=rider, status=SquadMember.Status.MEMBER)

    client.force_login(captain)
    url = reverse("events:squad_assign", args=[event.pk])
    client.post(url, {"signup_id": signup.pk, "squad_id": 0, "remove_squad_id": theirs.pk})
    assert SquadMember.objects.filter(squad=theirs, user=rider).exists()  # not theirs to remove

    client.post(url, {"signup_id": signup.pk, "squad_id": 0, "remove_squad_id": mine.pk})
    assert not SquadMember.objects.filter(squad=mine, user=rider).exists()  # own squad: allowed


@pytest.mark.django_db
def test_captain_cannot_clear_a_rider_from_every_squad(client, event, user_model) -> None:
    """Removing from all squads is event-wide, so it stays with full squad managers."""
    captain = _member(user_model, "cap")
    rider = _member(user_model, "rider")
    mine = Squad.objects.create(event=event, name="Alpha")
    mine.captains.add(captain)
    other = Squad.objects.create(event=event, name="Bravo")
    signup = EventSignup.objects.create(event=event, user=rider)
    SquadMember.objects.create(squad=mine, user=rider, status=SquadMember.Status.MEMBER)
    SquadMember.objects.create(squad=other, user=rider, status=SquadMember.Status.MEMBER)

    client.force_login(captain)
    client.post(reverse("events:squad_assign", args=[event.pk]), {"signup_id": signup.pk, "squad_id": 0})

    assert SquadMember.objects.filter(user=rider).count() == 2


@pytest.mark.django_db
def test_add_rider_picker_groups_unassigned_first_then_by_squad(client, event, user_model) -> None:
    admin = user_model.objects.create_user(
        username="ea", email="ea@example.test",
        permission_overrides={"team_member": True, "event_admin": True},
    )
    alpha = Squad.objects.create(event=event, name="Alpha")
    bravo = Squad.objects.create(event=event, name="Bravo")

    free = _member(user_model, "free")
    in_bravo = _member(user_model, "inbravo")
    EventSignup.objects.create(event=event, user=free)
    EventSignup.objects.create(event=event, user=in_bravo)
    SquadMember.objects.create(squad=bravo, user=in_bravo, status=SquadMember.Status.MEMBER)

    client.force_login(admin)
    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    alpha_squad = next(s for s in resp.context["squads"] if s.pk == alpha.pk)
    labels = [label for label, _ in alpha_squad.available_groups]
    assert labels == ["Unassigned", "Bravo"]  # unassigned first, then squads A-Z
    groups = dict(alpha_squad.available_groups)
    assert [s.user_id for s in groups["Unassigned"]] == [free.pk]
    assert [s.user_id for s in groups["Bravo"]] == [in_bravo.pk]
    assert "<optgroup label=\"Unassigned\">" in resp.content.decode()


@pytest.mark.django_db
def test_captain_sees_the_add_rider_control_for_their_squad(client, event, user_model) -> None:
    captain = _member(user_model, "cap")
    rider = _member(user_model, "rider")
    squad = Squad.objects.create(event=event, name="Alpha")
    squad.captains.add(captain)
    EventSignup.objects.create(event=event, user=rider)

    client.force_login(captain)
    resp = client.get(reverse("events:squad_manage", args=[event.pk]))

    alpha = next(s for s in resp.context["squads"] if s.pk == squad.pk)
    assert alpha.can_manage is True
    assert "+ Add rider" in resp.content.decode()
