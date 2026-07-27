"""The scheduled-race modal's "everyone else on this squad" picker.

The modal builds its rider list from the availability pool for a slot, so anyone
who did not mark themselves available could not be picked. The view now also ships
the full squad roster, which the modal renders in a separate disclosure.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events.models import AvailabilityGrid, Event, Squad, SquadMember


@pytest.fixture
def grid_setup(db, user_model, team_member):
    """Build an event/squad/grid with the captain able to manage availability.

    Returns:
        A ``(event, squad, grid)`` tuple; ``team_member`` is the squad captain.

    """
    today = timezone.now().date()
    event = Event.objects.create(
        title="Series", start_date=today - timedelta(days=1), end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(event=event, name="Alpha")
    squad.captains.add(team_member)
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today,
        end_date=today + timedelta(days=1),
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    return event, squad, grid


def _results_url(event, squad, grid):
    return reverse("events:availability_results", args=[event.pk, squad.pk, grid.pk])


@pytest.mark.django_db
def test_roster_json_includes_members_captains_and_vice_captains(auth_client, grid_setup, user_model, team_member):
    """The picker must offer the whole squad, not just MEMBER rows.

    The page's own non-responder table is MEMBER-only, but captains and
    vice-captains race too, so the modal uses the canonical roster helper.
    """
    event, squad, grid = grid_setup
    member = user_model.objects.create(username="mem", first_name="Mem", zwid=7001)
    vice = user_model.objects.create(username="vice", first_name="Vice", zwid=7002)
    outsider = user_model.objects.create(username="out", first_name="Out", zwid=7003)
    SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)
    squad.vice_captains.add(vice)

    resp = auth_client.get(_results_url(event, squad, grid))
    roster = resp.context["squad_roster_ids_json"]

    assert resp.status_code == 200
    assert str(member.pk) in roster
    assert str(vice.pk) in roster
    assert str(team_member.pk) in roster  # the captain
    assert str(outsider.pk) not in roster


@pytest.mark.django_db
def test_roster_members_have_display_data_even_without_a_response(auth_client, grid_setup, user_model):
    """The modal renders names from user_data_json, so non-responders need entries."""
    event, squad, grid = grid_setup
    member = user_model.objects.create(username="mem2", first_name="Never", last_name="Responded", zwid=7101)
    SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)

    resp = auth_client.get(_results_url(event, squad, grid))

    assert f'"{member.pk}"' in resp.context["user_data_json"]
    assert "Never Responded" in resp.context["user_data_json"]


@pytest.mark.django_db
def test_a_pending_squad_membership_is_not_offered(auth_client, grid_setup, user_model):
    event, squad, grid = grid_setup
    pending = user_model.objects.create(username="pend", first_name="Pend", zwid=7201)
    SquadMember.objects.create(squad=squad, user=pending, status=SquadMember.Status.PENDING)

    resp = auth_client.get(_results_url(event, squad, grid))

    assert str(pending.pk) not in resp.context["squad_roster_ids_json"]


@pytest.mark.django_db
def test_the_modal_renders_the_others_disclosure(auth_client, grid_setup):
    """Both the rider and substitute pickers get an "everyone else" section."""
    event, squad, grid = grid_setup

    resp = auth_client.get(_results_url(event, squad, grid))
    body = resp.content.decode()

    assert body.count("Everyone else on this squad") == 2
    assert 'id="slot-modal-others"' in body
    assert 'id="slot-modal-sub-others"' in body
    assert "const squadRosterIds" in body


@pytest.mark.django_db
def test_saving_accepts_a_rider_who_was_not_available(auth_client, grid_setup, user_model):
    """The whole feature rests on the save path not filtering by availability."""
    event, squad, grid = grid_setup
    member = user_model.objects.create(username="mem3", first_name="Not", last_name="Free", zwid=7301)
    SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)
    today = timezone.now().date()

    resp = auth_client.post(
        reverse("events:slot_selection_create", args=[event.pk, squad.pk, grid.pk]),
        {
            "name": "Race A",
            "slot_date": today.isoformat(),
            "slot_time": "18:00",
            "selected_users": [str(member.pk)],
        },
        HTTP_HX_REQUEST="true",
    )

    assert resp.status_code == 200
    selection = grid.slot_selections.get(name="Race A")
    assert list(selection.selected_users.values_list("pk", flat=True)) == [member.pk]
