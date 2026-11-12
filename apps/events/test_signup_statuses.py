"""Signup statuses: each event's own list, the status dialog, and where every rider stands.

Guards the rules in ``apps/events/signup_status.py``, the two pages in
``signup_status_views.py``, and the existing pages that read them: the event page's signup
table and the rider's own view, Assign Riders, Add riders, the Manage Squads picker, the
squad assignment itself and the invite link.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GuildMember
from apps.events import signup_status
from apps.events.models import (
    AvailabilityGrid,
    AvailabilitySlotSelection,
    Event,
    EventSignup,
    SignupStatus,
    Squad,
    SquadMember,
)
from apps.events.signup_status_views import DIALOG_BODY_ID
from conftest import _make_user

DIALOG = {"HX-Request": "true", "HX-Target": DIALOG_BODY_ID}


def _event(title: str = "Winter League", **extra) -> Event:
    """Build a visible event with signups open.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title=title, start_date=today, end_date=today + timedelta(days=60), visible=True, signups_open=True, **extra
    )


@pytest.fixture
def event(db) -> Event:
    """Build the event most tests use.

    Returns:
        The event.

    """
    return _event()


def _rider(user_model, name: str, *, discord_id: str | None = None, **extra):
    """Make a team member.

    Returns:
        The rider.

    """
    return _make_user(
        user_model, username=name, permissions={"team_member": True}, discord_id=discord_id or "", **extra
    )


def _left(user) -> None:
    """Record that a rider has left the team's Discord server, as the guild sync does."""
    GuildMember.objects.create(discord_id=user.discord_id, username=user.username, date_left=timezone.now())


def _status(event: Event, name: str = "Waiting · Timezone", kind: str = SignupStatus.Kind.WAITING, **extra):
    """Add a status to an event's list.

    Returns:
        The status.

    """
    return SignupStatus.objects.create(event=event, name=name, kind=kind, **extra)


def _signup(event: Event, user, **extra) -> EventSignup:
    """Sign a rider up.

    Returns:
        The signup.

    """
    return EventSignup.objects.create(event=event, user=user, **extra)


def _status_url(signup: EventSignup) -> str:
    return reverse("events:signup_status", args=[signup.event_id, signup.pk])


def _statuses_url(event: Event) -> str:
    return reverse("events:signup_statuses", args=[event.pk])


def _page(client, name: str, *args, **params) -> str:
    response = client.get(reverse(name, args=args), params)
    assert response.status_code == 200
    return response.content.decode()


# --- Where a rider stands ------------------------------------------------------------------------


@pytest.mark.django_db
def test_withdrawn_outranks_leaving_which_outranks_a_set_aside_status(event, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    waiting = _status(event)
    aside = _status(event, name="Asked, no reply", kind=SignupStatus.Kind.SET_ASIDE)
    bucket = signup_status.bucket

    assert bucket(signup, None, in_squad=False, departed=False) == signup_status.NEEDS
    assert bucket(signup, None, in_squad=True, departed=False) == signup_status.IN_SQUAD
    assert bucket(signup, waiting, in_squad=False, departed=False) == signup_status.WAITING
    # Joining a squad normally clears a waiting status; one that survives does not hide the place.
    assert bucket(signup, waiting, in_squad=True, departed=False) == signup_status.IN_SQUAD
    assert bucket(signup, aside, in_squad=True, departed=False) == signup_status.SET_ASIDE
    assert bucket(signup, aside, in_squad=True, departed=True) == signup_status.LEFT
    signup.status = EventSignup.Status.WITHDRAWN
    assert bucket(signup, None, in_squad=False, departed=True) == signup_status.WITHDRAWN


@pytest.mark.django_db
def test_a_rider_has_left_when_the_guild_sync_marked_their_discord_id(user_model):
    gone = _rider(user_model, "gone", discord_id="111")
    here = _rider(user_model, "here", discord_id="222")
    nobody = _rider(user_model, "nobody")
    _left(gone)
    GuildMember.objects.create(discord_id="222", username="here")

    assert signup_status.departed_user_ids([gone, here, nobody]) == {gone.pk}


@pytest.mark.django_db
def test_a_new_event_has_no_statuses(client, event, event_admin):
    client.force_login(event_admin)

    body = _page(client, "events:signup_statuses", event.pk)

    assert not event.signup_statuses.exists()
    assert "This event has no statuses of its own yet" in body


@pytest.mark.django_db
def test_copying_skips_names_already_here_in_any_case_and_retired_ones(event):
    other = _event("Autumn Crits")
    _status(other, name="Waiting · Timezone")
    _status(other, name="Reserve", colour=SignupStatus.Colour.GREEN, rider_message="You're a reserve.")
    _status(other, name="Old", retired=True)
    _status(event, name="waiting · timezone")

    added, skipped = signup_status.copy_statuses(other, event)

    assert (added, skipped) == (1, 1)
    copied = SignupStatus.objects.get(event=event, name="Reserve")
    assert copied.colour == SignupStatus.Colour.GREEN
    assert copied.rider_message == "You're a reserve."
    assert copied.order == 1
    assert not SignupStatus.objects.filter(event=event, name="Old").exists()


# --- The status dialog -------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_dialog_answers_its_fetch_with_the_form_alone(client, event, event_admin, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    _status(event)
    client.force_login(event_admin)

    response = client.get(_status_url(signup), {"next": "/events/"}, headers=DIALOG)

    assert response.status_code == 200
    templates = {template.name for template in response.templates}
    assert "events/_signup_status_form.html" in templates
    assert "base.html" not in templates
    assert "HX-Request" in response["Vary"]
    body = response.content.decode()
    assert 'id="status-dialog-title"' in body
    assert "Waiting · Timezone" in body
    assert 'name="next" value="/events/"' in body


@pytest.mark.django_db
def test_without_javascript_the_status_is_a_page_of_its_own(client, event, event_admin, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    client.force_login(event_admin)

    response = client.get(_status_url(signup))

    assert "events/signup_status.html" in {template.name for template in response.templates}
    body = response.content.decode()
    assert "<h1" in body
    # No statuses yet: the dialog still offers no status and Withdrawn, and says where to add more.
    assert "This event has no statuses of its own yet" in body
    assert f'href="{_statuses_url(event)}"' in body


@pytest.mark.django_db
def test_a_squad_captain_sees_statuses_but_cannot_set_them(client, event, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    captain = _rider(user_model, "cap")
    Squad.objects.create(event=event, name="A1").captains.add(captain)
    client.force_login(captain)

    assert client.get(_status_url(signup), headers=DIALOG).status_code == 403
    assert client.get(_status_url(signup)).status_code == 302
    client.post(_status_url(signup), {"choice": "withdrawn"})

    signup.refresh_from_db()
    assert signup.status == EventSignup.Status.REGISTERED


@pytest.mark.django_db
def test_the_head_captain_may_set_a_status(client, user_model):
    event = _event(head_captain_role_id=555)
    signup = _signup(event, _rider(user_model, "ana"))
    waiting = _status(event)
    client.force_login(_rider(user_model, "head", discord_roles={"555": "Head Captain"}))

    client.post(_status_url(signup), {"choice": str(waiting.pk)})

    signup.refresh_from_db()
    assert signup.staff_status == waiting


@pytest.mark.django_db
def test_setting_a_status_records_who_and_when_and_goes_back(client, event, event_admin, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    waiting = _status(event)
    client.force_login(event_admin)

    response = client.post(
        _status_url(signup), {"choice": str(waiting.pk), "note": "No APAC squad yet", "next": "/events/?tab=signups"}
    )

    assert response.status_code == 302
    assert response.url == "/events/?tab=signups"
    signup.refresh_from_db()
    assert signup.staff_status == waiting
    assert signup.status_note == "No APAC squad yet"
    assert signup.status_set_by == event_admin
    assert signup.status_set_at is not None


@pytest.mark.django_db
def test_next_must_stay_on_this_site(client, event, event_admin, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    client.force_login(event_admin)

    response = client.post(_status_url(signup), {"choice": "", "note": "x", "next": "https://elsewhere.example/"})

    assert response.url == reverse("events:event_detail", args=[event.pk]) + "?tab=signups"


@pytest.mark.django_db
def test_withdrawing_frees_squad_places_and_clears_the_status(client, event, event_admin, user_model):
    rider = _rider(user_model, "ana")
    squad = Squad.objects.create(event=event, name="A1")
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    signup = _signup(event, rider, staff_status=_status(event, kind=SignupStatus.Kind.SET_ASIDE, name="Aside"))
    client.force_login(event_admin)

    client.post(_status_url(signup), {"choice": "withdrawn"})

    signup.refresh_from_db()
    assert signup.status == EventSignup.Status.WITHDRAWN
    assert signup.staff_status is None
    assert not SquadMember.objects.filter(user=rider).exists()


def _race(squad: Squad, days: int, *riders, subs=()) -> AvailabilitySlotSelection:
    """Schedule a race ``days`` from today at 19:00 UTC, with riders picked and substitutes.

    Returns:
        The race.

    """
    today = date.today()
    grid = squad.availability_grids.first() or AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today - timedelta(days=7),
        end_date=today + timedelta(days=7),
        start_time="18:00",
        end_time="21:00",
        slot_duration=60,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    starts = datetime.combine(today + timedelta(days=days), time(19, 0), tzinfo=UTC)
    race = AvailabilitySlotSelection.objects.create(
        grid=grid, name=f"Race {days}", slot_date=starts.date(), slot_time=starts.strftime("%H:%M")
    )
    race.selected_users.add(*riders)
    race.substitutes.add(*subs)
    return race


@pytest.mark.django_db
def test_withdrawing_takes_the_rider_off_upcoming_lineups_and_keeps_raced_ones(
    client, event, event_admin, user_model
):
    ana = _rider(user_model, "ana")
    bea = _rider(user_model, "bea")
    squad = Squad.objects.create(event=event, name="A1")
    picked = _race(squad, 2, ana, bea)
    on_the_bench = _race(squad, 3, bea, subs=[ana])
    raced = _race(squad, -2, ana, bea)
    signup = _signup(event, ana)
    client.force_login(event_admin)

    response = client.post(_status_url(signup), {"choice": "withdrawn"}, follow=True)

    assert list(picked.selected_users.all()) == [bea]
    assert not on_the_bench.substitutes.exists()
    assert set(raced.selected_users.all()) == {ana, bea}  # the record of who rode it
    sent = " ".join(str(message) for message in response.context["messages"])
    assert "They're off the lineup of 2 upcoming races." in sent


@pytest.mark.django_db
def test_the_edit_pages_withdraw_and_deleting_your_own_signup_leave_lineups_too(
    client, event, event_admin, user_model
):
    ana = _rider(user_model, "ana")
    cy = _rider(user_model, "cy")
    race = _race(Squad.objects.create(event=event, name="A1"), 2, ana, cy)
    signup = _signup(event, ana)
    _signup(event, cy)

    client.force_login(event_admin)
    client.post(reverse("events:event_signup_withdraw", args=[event.pk, signup.pk]))
    client.force_login(cy)
    client.post(reverse("events:event_signup_delete", args=[event.pk]))

    assert not race.selected_users.exists()
    assert not EventSignup.objects.filter(event=event, user=cy).exists()


@pytest.mark.django_db
def test_bringing_a_withdrawn_rider_back_asks_the_signup_requirements(
    client, event, event_admin, user_model, complete_profile
):
    rider = _rider(user_model, "ana")
    signup = _signup(event, rider, status=EventSignup.Status.WITHDRAWN)
    client.force_login(event_admin)

    client.post(_status_url(signup), {"choice": ""})
    signup.refresh_from_db()
    assert signup.status == EventSignup.Status.WITHDRAWN, "an incomplete profile keeps them off the event"

    complete_profile(rider)
    client.post(_status_url(signup), {"choice": ""})
    signup.refresh_from_db()
    assert signup.status == EventSignup.Status.REGISTERED


@pytest.mark.django_db
def test_a_retired_status_is_refused_unless_the_signup_already_has_it(client, event, event_admin, user_model):
    retired = _status(event, retired=True)
    has_it = _signup(event, _rider(user_model, "ana"), staff_status=retired)
    fresh = _signup(event, _rider(user_model, "bea"))
    client.force_login(event_admin)

    client.post(_status_url(fresh), {"choice": str(retired.pk)})
    client.post(_status_url(has_it), {"choice": str(retired.pk), "note": "kept"})

    fresh.refresh_from_db()
    has_it.refresh_from_db()
    assert fresh.staff_status is None
    assert has_it.staff_status == retired
    assert has_it.status_note == "kept"
    # The dialog shows the retired status as chosen to the signup that has it, and to nobody else.
    assert "Waiting · Timezone" in client.get(_status_url(has_it), headers=DIALOG).content.decode()
    assert "Waiting · Timezone" not in client.get(_status_url(fresh), headers=DIALOG).content.decode()


@pytest.mark.django_db
def test_a_status_from_another_event_is_refused(client, event, event_admin, user_model):
    elsewhere = _status(_event("Elsewhere"))
    signup = _signup(event, _rider(user_model, "ana"))
    client.force_login(event_admin)

    client.post(_status_url(signup), {"choice": str(elsewhere.pk)})

    signup.refresh_from_db()
    assert signup.staff_status is None


@pytest.mark.django_db
def test_a_rider_who_left_can_only_be_withdrawn_or_have_their_note_saved(client, event, event_admin, user_model):
    rider = _rider(user_model, "ana", discord_id="333")
    _left(rider)
    signup = _signup(event, rider)
    waiting = _status(event)
    client.force_login(event_admin)

    body = client.get(_status_url(signup), headers=DIALOG).content.decode()
    assert "left the team's Discord server" in body
    client.post(_status_url(signup), {"choice": str(waiting.pk)})
    signup.refresh_from_db()
    assert signup.staff_status is None

    client.post(_status_url(signup), {"choice": "keep", "note": "Joined another team"})
    signup.refresh_from_db()
    assert signup.status_note == "Joined another team"
    assert signup.status == EventSignup.Status.REGISTERED

    client.post(_status_url(signup), {"choice": "withdrawn", "note": "Joined another team"})
    signup.refresh_from_db()
    assert signup.status == EventSignup.Status.WITHDRAWN


@pytest.mark.django_db
def test_an_overlong_note_saves_nothing(client, event, event_admin, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    waiting = _status(event)
    client.force_login(event_admin)

    client.post(_status_url(signup), {"choice": str(waiting.pk), "note": "x" * 501})

    signup.refresh_from_db()
    assert signup.staff_status is None
    assert signup.status_note == ""


@pytest.mark.django_db
def test_the_note_and_status_names_never_reach_logfire(client, event, event_admin, user_model):
    signup = _signup(event, _rider(user_model, "ana"))
    waiting = _status(event, name="Quiet label")
    client.force_login(event_admin)

    with patch("apps.events.signup_status_views.logfire") as logfire:
        client.post(_status_url(signup), {"choice": str(waiting.pk), "note": "private words"})

    calls = repr(logfire.mock_calls)
    assert "Signup status set" in calls
    assert "private words" not in calls
    assert "Quiet label" not in calls


# --- An event's list ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_list_is_for_event_admins(client, event, team_member):
    client.force_login(team_member)

    response = client.get(_statuses_url(event))

    assert response.status_code == 302
    assert response.url == reverse("events:event_detail", args=[event.pk])


@pytest.mark.django_db
def test_adding_a_status(client, event, event_admin):
    client.force_login(event_admin)

    client.post(
        _statuses_url(event),
        {
            "action": "add",
            "add-name": "  Reserve  ",
            "add-kind": SignupStatus.Kind.SET_ASIDE,
            "add-colour": SignupStatus.Colour.GREEN,
            "add-rider_message": "You're a reserve for now.",
        },
    )

    status = SignupStatus.objects.get(event=event)
    assert status.name == "Reserve"
    assert status.kind == SignupStatus.Kind.SET_ASIDE
    assert status.colour == SignupStatus.Colour.GREEN
    assert status.rider_message == "You're a reserve for now."


@pytest.mark.django_db
def test_names_are_unique_on_an_event_in_any_case(client, event, event_admin):
    _status(event, name="Reserve")
    client.force_login(event_admin)

    response = client.post(
        _statuses_url(event),
        {"action": "add", "add-name": "reserve", "add-kind": "waiting", "add-colour": "info"},
    )

    assert response.status_code == 200
    assert "already has a status with that name" in response.content.decode()
    assert SignupStatus.objects.filter(event=event).count() == 1


@pytest.mark.django_db
def test_moving_stays_within_a_kind(client, event, event_admin):
    first = _status(event, name="First", order=0)
    aside = _status(event, name="Aside", kind=SignupStatus.Kind.SET_ASIDE, order=1)
    second = _status(event, name="Second", order=2)
    client.force_login(event_admin)

    # Second's neighbour in the list is Aside, but it moves past it to swap with First.
    client.post(_statuses_url(event), {"action": "up", "status": second.pk})

    names = list(
        SignupStatus.objects.filter(event=event, kind=SignupStatus.Kind.WAITING).values_list("name", flat=True)
    )
    assert names == ["Second", "First"]
    assert SignupStatus.objects.filter(event=event, kind=SignupStatus.Kind.SET_ASIDE).get() == aside
    assert first.pk != second.pk


@pytest.mark.django_db
def test_a_status_in_use_is_retired_not_deleted(client, event, event_admin, user_model):
    status = _status(event)
    _signup(event, _rider(user_model, "ana"), staff_status=status)
    unused = _status(event, name="Unused")
    client.force_login(event_admin)

    client.post(_statuses_url(event), {"action": "delete", "status": status.pk})
    assert SignupStatus.objects.filter(pk=status.pk).exists()
    client.post(_statuses_url(event), {"action": "retire", "status": status.pk})
    status.refresh_from_db()
    assert status.retired
    client.post(_statuses_url(event), {"action": "delete", "status": unused.pk})
    assert not SignupStatus.objects.filter(pk=unused.pk).exists()


@pytest.mark.django_db
def test_copying_from_another_event_on_the_page(client, event, event_admin):
    other = _event("Autumn Crits")
    _status(other, name="Reserve")
    client.force_login(event_admin)

    body = _page(client, "events:signup_statuses", event.pk)
    assert f'<option value="{other.pk}">Autumn Crits</option>' in body
    client.post(_statuses_url(event), {"action": "copy", "source": other.pk})

    assert list(event.signup_statuses.values_list("name", flat=True)) == ["Reserve"]


@pytest.mark.django_db
def test_the_event_edit_page_links_the_list(client, event, event_admin):
    client.force_login(event_admin)

    body = _page(client, "events:event_edit", event.pk)

    assert f'href="{_statuses_url(event)}"' in body
    assert "Add statuses" in body
    _status(event)
    assert "Edit statuses (1)" in _page(client, "events:event_edit", event.pk)


# --- The event page ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_signup_table_shows_where_each_rider_stands(client, event, event_admin, user_model):
    waiting = _status(event)
    _signup(event, _rider(user_model, "ana"), staff_status=waiting, status_note="No APAC squad yet")
    _signup(event, _rider(user_model, "bea"), status=EventSignup.Status.WITHDRAWN)
    gone = _rider(user_model, "cal", discord_id="444")
    _left(gone)
    squad = Squad.objects.create(event=event, name="B1")
    SquadMember.objects.create(squad=squad, user=gone, status=SquadMember.Status.MEMBER)
    _signup(event, gone)
    client.force_login(event_admin)

    body = _page(client, "events:event_detail", event.pk)

    for bucket in ("waiting", "withdrawn", "left"):
        assert f'data-status-bucket="{bucket}"' in body
    assert 'id="status-filter"' in body
    assert ">Left the team</span>" in body
    assert "Still has a squad place" in body
    assert "Needs attention" in body
    assert "No APAC squad yet" in body, "event staff see their note"
    assert 'id="status-dialog"' in body


@pytest.mark.django_db
def test_squad_captains_see_statuses_but_never_the_staff_note(client, event, user_model):
    waiting = _status(event)
    _signup(event, _rider(user_model, "ana"), staff_status=waiting, status_note="No APAC squad yet")
    captain = _rider(user_model, "cap")
    Squad.objects.create(event=event, name="A1").captains.add(captain)
    client.force_login(captain)

    body = _page(client, "events:event_detail", event.pk)

    assert "Waiting · Timezone" in body
    assert "No APAC squad yet" not in body
    assert 'id="status-dialog"' not in body


@pytest.mark.django_db
def test_a_rider_sees_their_status_message_and_never_the_note(client, event, user_model):
    status = _status(event, rider_message="A captain will message you on Discord when a squad opens.")
    rider = _rider(user_model, "ana")
    _signup(event, rider, staff_status=status, status_note="Keep an eye on this one")
    client.force_login(rider)

    body = _page(client, "events:event_detail", event.pk)

    assert "A captain will message you on Discord when a squad opens." in body
    assert "Keep an eye on this one" not in body


@pytest.mark.django_db
def test_a_status_without_a_message_stays_with_event_staff(client, event, user_model):
    rider = _rider(user_model, "ana")
    _signup(event, rider, staff_status=_status(event, name="Asked, no reply"))
    client.force_login(rider)

    assert "Asked, no reply" not in _page(client, "events:event_detail", event.pk)


@pytest.mark.django_db
def test_a_withdrawn_rider_can_sign_up_again(client, event, user_model, complete_profile):
    rider = complete_profile(_rider(user_model, "ana"))
    signup = _signup(event, rider, status=EventSignup.Status.WITHDRAWN)
    client.force_login(rider)

    assert "your signup was withdrawn" in _page(client, "events:event_detail", event.pk)
    client.post(reverse("events:event_signup", args=[event.pk]))

    signup.refresh_from_db()
    assert signup.status == EventSignup.Status.REGISTERED
    assert EventSignup.objects.filter(event=event, user=rider).count() == 1


# --- Placing riders ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_neither_a_withdrawn_rider_nor_one_who_left_can_be_put_in_a_squad(client, event, event_admin, user_model):
    squad = Squad.objects.create(event=event, name="A1")
    out = _signup(event, _rider(user_model, "ana"), status=EventSignup.Status.WITHDRAWN)
    gone_rider = _rider(user_model, "bea", discord_id="555")
    _left(gone_rider)
    gone = _signup(event, gone_rider)
    client.force_login(event_admin)

    for signup in (out, gone):
        client.post(reverse("events:squad_assign", args=[event.pk]), {"signup_id": signup.pk, "squad_id": squad.pk})

    assert not SquadMember.objects.filter(squad=squad).exists()


@pytest.mark.django_db
def test_joining_a_squad_clears_a_waiting_status_but_not_a_set_aside_one(client, event, event_admin, user_model):
    squad = Squad.objects.create(event=event, name="A1")
    waiting = _signup(event, _rider(user_model, "ana"), staff_status=_status(event))
    aside = _signup(event, _rider(user_model, "bea"), staff_status=_status(event, "Aside", SignupStatus.Kind.SET_ASIDE))
    client.force_login(event_admin)

    for signup in (waiting, aside):
        client.post(reverse("events:squad_assign", args=[event.pk]), {"signup_id": signup.pk, "squad_id": squad.pk})

    waiting.refresh_from_db()
    aside.refresh_from_db()
    assert SquadMember.objects.filter(squad=squad).count() == 2
    assert waiting.staff_status is None
    assert aside.staff_status is not None


@pytest.mark.django_db
def test_an_invite_link_clears_a_waiting_status(client, event, user_model, complete_profile):
    squad = Squad.objects.create(event=event, name="A1", invite_token=uuid.uuid4())
    rider = complete_profile(_rider(user_model, "ana"))
    signup = _signup(event, rider, staff_status=_status(event))
    client.force_login(rider)

    client.post(reverse("events:squad_invite", args=[squad.invite_token]))

    signup.refresh_from_db()
    assert SquadMember.objects.filter(squad=squad, user=rider).exists()
    assert signup.staff_status is None


@pytest.mark.django_db
def test_the_manage_squads_picker_offers_only_riders_who_can_be_placed(client, event, event_admin, user_model):
    squad = Squad.objects.create(event=event, name="A1")
    needs = _signup(event, _rider(user_model, "needs"))
    waiting = _signup(event, _rider(user_model, "waits"), staff_status=_status(event))
    _signup(event, _rider(user_model, "out"), status=EventSignup.Status.WITHDRAWN)
    _signup(event, _rider(user_model, "aside"), staff_status=_status(event, "Aside", SignupStatus.Kind.SET_ASIDE))
    gone = _rider(user_model, "gone", discord_id="666")
    _left(gone)
    _signup(event, gone)
    client.force_login(event_admin)

    response = client.get(reverse("events:squad_manage", args=[event.pk]))

    panel = next(s for s in response.context["squads"] if s.pk == squad.pk)
    groups = dict(panel.available_groups)
    assert list(groups) == ["Needs a squad", "Waiting for a squad"]
    assert [s.pk for s in groups["Needs a squad"]] == [needs.pk]
    assert [s.pk for s in groups["Waiting for a squad"]] == [waiting.pk]
    assert "Waits Test &middot; Waiting · Timezone" in response.content.decode()


@pytest.mark.django_db
def test_add_riders_leaves_out_who_left_and_names_who_waits_for_this_timezone(client, event, event_admin, user_model):
    squad = Squad.objects.create(event=event, name="C3", squad_timezone="Australia/Sydney")
    _signup(event, _rider(user_model, "waits"), staff_status=_status(event), signup_timezone=["Australia/Sydney"])
    _signup(event, _rider(user_model, "elsewhere"), staff_status=_status(event, "Waiting · Category"))
    gone = _rider(user_model, "gone", discord_id="777")
    _left(gone)
    _signup(event, gone)
    client.force_login(event_admin)

    response = client.get(reverse("events:squad_add_riders", args=[event.pk, squad.pk]))

    names = [entry["user"].username for entry in response.context["enriched_signups"]]
    assert "gone" not in names
    assert [entry["user"].username for entry in response.context["waiting_here"]] == ["waits"]
    body = response.content.decode()
    assert "is waiting for a squad in Australia/Sydney" in body
    assert "1 rider who left the team" in body


@pytest.mark.django_db
def test_assign_riders_offers_no_squad_picker_to_a_withdrawn_rider(client, event, event_admin, user_model):
    Squad.objects.create(event=event, name="A1")
    out = _signup(event, _rider(user_model, "ana"), status=EventSignup.Status.WITHDRAWN)
    live = _signup(event, _rider(user_model, "bea"))
    client.force_login(event_admin)

    body = _page(client, "events:squad_assign_page", event.pk)

    def cell(signup):
        start = body.index(f'id="squad-cell-{signup.pk}"')
        return body[start : body.index("</td>", start)]

    assert "<select" not in cell(out)
    assert "<select" in cell(live)
    assert 'id="filter-status"' in body
    assert "Not shown while Taking part is chosen" in body


@pytest.mark.django_db
def test_the_squad_panels_mark_a_member_who_left(client, event, event_admin, user_model):
    squad = Squad.objects.create(event=event, name="B1")
    gone = _rider(user_model, "gone", discord_id="888")
    _left(gone)
    SquadMember.objects.create(squad=squad, user=gone, status=SquadMember.Status.MEMBER)
    _signup(event, gone)
    client.force_login(event_admin)

    for name in ("events:squad_assign_page", "events:squad_manage"):
        body = _page(client, name, event.pk)
        panel = body[body.index(f'id="squad-panel-{squad.pk}"') :]
        assert ">Left the team</span>" in panel[: panel.index("</ul>")], name
