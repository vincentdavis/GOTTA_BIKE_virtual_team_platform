"""Squad-based edit permission on TTT plans.

Mirrors the ladder planner's ``edit_squad`` grant: the creator picks a squad, and
that squad's roster (members, captains, vice-captains) can edit the plan too.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, Squad, SquadMember
from apps.ttt_planner import views as ttt_views
from apps.ttt_planner.models import TttPlan


def _event(title="Series", *, days_to_end=7, visible=True):
    """Create an event ending ``days_to_end`` days from today.

    Returns:
        The created Event.

    """
    today = timezone.now().date()
    return Event.objects.create(
        title=title,
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=days_to_end),
        visible=visible,
    )


def _plan(owner, **kwargs):
    return TttPlan.objects.create(created_by=owner, target_speed_kph=40, **kwargs)


# --- _can_edit ---------------------------------------------------------------


@pytest.mark.django_db
def test_owner_and_superuser_can_edit(team_member, superuser):
    plan = _plan(team_member)
    assert ttt_views._can_edit(plan, team_member) is True
    assert ttt_views._can_edit(plan, superuser) is True


@pytest.mark.django_db
def test_edit_squad_grants_the_whole_roster(user_model):
    owner = user_model.objects.create(username="owner", zwid=9001)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(owner, edit_squad=squad)

    cap = user_model.objects.create(username="cap", zwid=9002)
    vice = user_model.objects.create(username="vice", zwid=9003)
    member = user_model.objects.create(username="mem", zwid=9004)
    outsider = user_model.objects.create(username="out", zwid=9005)
    squad.captains.add(cap)
    squad.vice_captains.add(vice)
    SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)

    assert ttt_views._can_edit(plan, cap) is True
    assert ttt_views._can_edit(plan, vice) is True
    assert ttt_views._can_edit(plan, member) is True
    assert ttt_views._can_edit(plan, outsider) is False


@pytest.mark.django_db
def test_no_edit_squad_means_no_extra_grant(user_model, team_member):
    """A plan without an edit squad stays owner-only."""
    owner = user_model.objects.create(username="owner2", zwid=9101)
    plan = _plan(owner)

    assert plan.edit_squad_id is None
    assert ttt_views._can_edit(plan, team_member) is False


@pytest.mark.django_db
def test_a_pending_squad_membership_does_not_grant_edit(user_model, team_member):
    """Only MEMBER status counts, matching squad_member_users."""
    owner = user_model.objects.create(username="owner3", zwid=9201)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(owner, edit_squad=squad)
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.PENDING)

    assert ttt_views._can_edit(plan, team_member) is False


# --- picking the squad -------------------------------------------------------


@pytest.mark.django_db
def test_plan_update_sets_and_clears_the_edit_squad(auth_client, team_member):
    plan = _plan(team_member)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    url = reverse("ttt_planner:update", args=[plan.pk])

    resp = auth_client.post(url, {"edit_squad": str(squad.pk)}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    assert resp.status_code == 200
    assert plan.edit_squad_id == squad.pk

    auth_client.post(url, {"edit_squad": ""}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    assert plan.edit_squad_id is None


@pytest.mark.django_db
def test_a_partial_update_leaves_the_edit_squad_alone(auth_client, team_member):
    """Controls that post a subset of the form must not clear the grant."""
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(team_member, edit_squad=squad)

    auth_client.post(reverse("ttt_planner:update", args=[plan.pk]), {"name": "Renamed"}, HTTP_HX_REQUEST="true")

    plan.refresh_from_db()
    assert plan.name == "Renamed"
    assert plan.edit_squad_id == squad.pk


@pytest.mark.django_db
def test_the_picker_offers_active_squads_to_the_owner(auth_client, team_member):
    plan = _plan(team_member)
    Squad.objects.create(event=_event("Live Series"), name="Alpha")

    resp = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk]))

    assert b'name="edit_squad"' in resp.content
    assert b"Live Series &mdash; Alpha" in resp.content or b"Live Series \xe2\x80\x94 Alpha" in resp.content


@pytest.mark.django_db
def test_a_squad_from_an_ended_event_stays_selected(auth_client, team_member):
    """The picker only lists active events, so an ended one needs a standalone option.

    Without it the select would render with nothing selected and the next settings
    save — which posts the whole form — would silently clear the grant.
    """
    today = timezone.now().date()
    past = Event.objects.create(
        title="Past Series", start_date=today - timedelta(days=30), end_date=today - timedelta(days=2), visible=True
    )
    squad = Squad.objects.create(event=past, name="Old Guard")
    plan = _plan(team_member, edit_squad=squad)

    resp = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk]))

    assert resp.context["edit_squad_active"] is False
    assert b"(event ended)" in resp.content


# --- the grant in action -----------------------------------------------------


@pytest.mark.django_db
def test_a_squad_member_can_update_a_plan_they_do_not_own(client, team_member, user_model):
    owner = user_model.objects.create(username="other-owner", zwid=8001)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(owner, edit_squad=squad)
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    resp = client.post(reverse("ttt_planner:update", args=[plan.pk]), {"name": "Renamed"}, HTTP_HX_REQUEST="true")

    plan.refresh_from_db()
    assert resp.status_code == 200
    assert plan.name == "Renamed"


@pytest.mark.django_db
def test_a_squad_member_can_edit_rider_rows(client, team_member, user_model):
    """The grant has to reach the row endpoints, not just plan settings."""
    from apps.ttt_planner.models import PlanRider

    owner = user_model.objects.create(username="other-owner2", zwid=8002)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(owner, edit_squad=squad)
    rider = PlanRider.objects.create(plan=plan, order=0, name="R", weight_kg=75, height_cm=180, ftp_w=250)
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    resp = client.post(
        reverse("ttt_planner:rider_update", args=[plan.pk, rider.pk]),
        {"zero_pull_submitted": "1", "zero_pull": "on"},
    )

    rider.refresh_from_db()
    assert resp.status_code == 200
    assert rider.zero_pull is True


@pytest.mark.django_db
def test_an_outsider_still_cannot_edit(client, team_member, user_model):
    owner = user_model.objects.create(username="other-owner3", zwid=8003)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(owner, edit_squad=squad)
    client.force_login(team_member)  # not in the squad

    resp = client.post(reverse("ttt_planner:update", args=[plan.pk]), {"name": "Nope"}, HTTP_HX_REQUEST="true")

    plan.refresh_from_db()
    assert resp.status_code == 403
    assert plan.name != "Nope"


@pytest.mark.django_db
def test_deleting_the_squad_leaves_the_plan_owner_only(user_model, team_member):
    """SET_NULL: losing the squad must not cascade the plan away."""
    owner = user_model.objects.create(username="owner4", zwid=9301)
    squad = Squad.objects.create(event=_event(), name="Alpha")
    plan = _plan(owner, edit_squad=squad)
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)

    squad.delete()

    plan.refresh_from_db()
    assert plan.edit_squad_id is None
    assert ttt_views._can_edit(plan, team_member) is False
    assert ttt_views._can_edit(plan, owner) is True
