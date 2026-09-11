"""The optional per-event signup requirements: a complete profile (on by default) and Race Verified.

Pinned here: the rule itself, and that it holds on every way onto an event -- the rider's own
signup, a squad invite link (which would otherwise also re-activate a withdrawn signup), and a
captain or admin adding members. A rider already on an event is not thrown off or re-checked.
"""

import re
import uuid
from datetime import date, timedelta
from unittest import mock

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup, Squad, SquadMember
from apps.events.signup_requirements import signup_blockers


def _event(**fields) -> Event:
    """Create an open event.

    Args:
        **fields: Event fields to override.

    Returns:
        The event.

    """
    today = date.today()
    values = {
        "title": "Summer Series",
        "start_date": today,
        "end_date": today + timedelta(days=30),
        "signups_open": True,
    }
    values.update(fields)
    return Event.objects.create(**values)


def _rider(user_model, name: str, *, complete: bool = True, race_ready: bool = False, complete_profile=None):
    """Create a team member.

    Args:
        user_model: The User class.
        name: Username, also the Discord username.
        complete: Whether to give them a complete profile.
        race_ready: Their Race Verified status.
        complete_profile: The ``complete_profile`` fixture, needed when ``complete``.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=name,
        email=f"{name}@example.test",
        discord_id=f"d-{name}",
        discord_username=name,
        first_name=name.title(),
        permission_overrides={"team_member": True},
        is_race_ready=race_ready,
    )
    if complete:
        complete_profile(user)
    return user


def _messages(response) -> list[str]:
    """Collect the flash messages on a followed response.

    Args:
        response: A response fetched with ``follow=True``.

    Returns:
        The message texts.

    """
    return [str(message) for message in response.context["messages"]]


# --- the rule -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_new_events_require_a_complete_profile_but_not_race_verified():
    """The defaults the team asked for."""
    event = _event()
    assert event.require_complete_profile_signup is True
    assert event.require_race_verified_signup is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("profile_required", "race_required", "complete", "race_ready", "expected"),
    [
        (True, False, True, False, []),
        (True, False, False, False, ["profile"]),
        (False, False, False, False, []),
        (False, True, True, False, ["race_verified"]),
        (False, True, True, True, []),
        (True, True, False, False, ["profile", "race_verified"]),
        (True, True, True, True, []),
    ],
)
def test_signup_blockers(user_model, complete_profile, profile_required, race_required, complete, race_ready, expected):
    """Each requirement blocks only when it is on and not met."""
    event = _event(require_complete_profile_signup=profile_required, require_race_verified_signup=race_required)
    rider = _rider(user_model, "ana", complete=complete, race_ready=race_ready, complete_profile=complete_profile)
    assert signup_blockers(event, rider) == expected


@pytest.mark.django_db
def test_race_verified_is_the_stored_status(user_model, complete_profile):
    """The cached is_race_ready the roster and Discord role use -- never recalculated here."""
    from apps.accounts.models import User

    event = _event(require_race_verified_signup=True)
    rider = _rider(user_model, "ana", race_ready=True, complete_profile=complete_profile)
    with mock.patch.object(User, "calculate_race_ready", side_effect=AssertionError("recalculated")):
        assert signup_blockers(event, rider) == []


@pytest.mark.django_db
def test_there_is_no_superuser_exception(superuser):
    """As with the event's availability requirement, a superuser signing up meets it or doesn't."""
    assert signup_blockers(_event(), superuser) == ["profile"]


# --- the rider's own signup ---------------------------------------------------------------


@pytest.mark.django_db
def test_an_incomplete_profile_cannot_sign_up_by_default(client, user_model):
    """Refused with a message, and nothing written."""
    event = _event()
    rider = _rider(user_model, "ana", complete=False)
    client.force_login(rider)

    response = client.post(reverse("events:event_signup", args=[event.pk]), follow=True)

    assert not EventSignup.objects.filter(event=event, user=rider).exists()
    assert _messages(response) == [
        "You can't sign up yet: this event requires a complete profile. Press Sign up to see how to fix it."
    ]


@pytest.mark.django_db
def test_with_the_profile_requirement_off_anyone_can_sign_up(client, user_model):
    """The requirement is per event, and can be switched off."""
    event = _event(require_complete_profile_signup=False)
    rider = _rider(user_model, "ana", complete=False)
    client.force_login(rider)

    client.post(reverse("events:event_signup", args=[event.pk]))

    assert EventSignup.objects.filter(event=event, user=rider, status=EventSignup.Status.REGISTERED).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("race_ready", [False, True])
def test_race_verified_requirement(client, user_model, complete_profile, race_ready):
    """Off by default; when on, only Race Verified riders get through."""
    event = _event(require_race_verified_signup=True)
    rider = _rider(user_model, "ana", race_ready=race_ready, complete_profile=complete_profile)
    client.force_login(rider)

    response = client.post(reverse("events:event_signup", args=[event.pk]), follow=True)

    assert EventSignup.objects.filter(event=event, user=rider).exists() is race_ready
    if not race_ready:
        assert "this event requires Race Verified status" in _messages(response)[0]


@pytest.mark.django_db
def test_both_requirements_are_named_together(client, user_model):
    """One message saying everything that is missing, not one fix at a time."""
    event = _event(require_race_verified_signup=True)
    client.force_login(_rider(user_model, "ana", complete=False))

    response = client.post(reverse("events:event_signup", args=[event.pk]), follow=True)

    assert "requires a complete profile and Race Verified status" in _messages(response)[0]


@pytest.mark.django_db
def test_a_refused_signup_is_logged(client, user_model):
    """So "why can't I sign up?" has an answer later."""
    event = _event()
    rider = _rider(user_model, "ana", complete=False)
    client.force_login(rider)

    with mock.patch("apps.events.views.logfire") as logfire:
        client.post(reverse("events:event_signup", args=[event.pk]))

    logfire.info.assert_any_call(
        "Event signup refused by signup requirements", event_id=event.pk, user_id=rider.pk, blockers=["profile"]
    )


# --- the event page -----------------------------------------------------------------------


def _signup_dialog(response) -> str:
    """Cut the signup dialog out of the event page.

    Args:
        response: The event page.

    Returns:
        The dialog's HTML.

    """
    body = response.content.decode()
    start = body.index('<dialog id="signup_modal"')
    return body[start : body.index("</dialog>", start)]


@pytest.mark.django_db
def test_a_blocked_rider_is_told_what_to_fix_instead_of_given_the_form(client, user_model):
    """Pressing Sign up says what is missing, with a link to fix each, and offers no form."""
    event = _event(require_race_verified_signup=True)
    client.force_login(_rider(user_model, "ana", complete=False))

    dialog = _signup_dialog(client.get(reverse("events:event_detail", args=[event.pk])))

    assert "Before you can sign up" in dialog
    assert f'href="{reverse("accounts:profile")}"' in dialog
    assert f'href="{reverse("accounts:verification")}"' in dialog
    assert "A complete profile" in dialog
    assert "Race Verified status" in dialog
    assert reverse("events:event_signup", args=[event.pk]) not in dialog


@pytest.mark.django_db
def test_an_eligible_rider_gets_the_form(client, user_model, complete_profile):
    """Nothing in the way, nothing said."""
    event = _event()
    client.force_login(_rider(user_model, "ana", complete_profile=complete_profile))

    dialog = _signup_dialog(client.get(reverse("events:event_detail", args=[event.pk])))

    assert "Before you can sign up" not in dialog
    assert f'action="{reverse("events:event_signup", args=[event.pk])}"' in dialog


@pytest.mark.django_db
def test_a_rider_already_signed_up_is_not_rechecked(client, user_model):
    """Signed up before the requirement (or before their profile lapsed): they stay on, and can edit."""
    event = _event()
    rider = _rider(user_model, "ana", complete=False)
    EventSignup.objects.create(event=event, user=rider)
    client.force_login(rider)

    response = client.get(reverse("events:event_detail", args=[event.pk]))

    assert response.context["signup_blockers"] == []
    assert "You&rsquo;re signed up &mdash; edit" in response.content.decode()


# --- a squad invite link -----------------------------------------------------------------


def _squad(event: Event) -> Squad:
    """Create a squad with an invite link.

    Args:
        event: Its event.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Alpha", invite_token=uuid.uuid4())


@pytest.mark.django_db
def test_an_invite_link_shows_what_to_fix_instead_of_join(client, user_model):
    """Joining signs you up, so the invite page says what the event needs first."""
    squad = _squad(_event())
    client.force_login(_rider(user_model, "ana", complete=False))

    body = client.get(reverse("events:squad_invite", args=[squad.invite_token])).content.decode()

    assert "Before you can join" in body
    assert f'href="{reverse("accounts:profile")}"' in body
    assert "Join Squad" not in body


@pytest.mark.django_db
def test_an_invite_link_is_not_a_way_round_the_requirements(client, user_model):
    """Posting the join anyway is refused: no signup, no squad place."""
    squad = _squad(_event())
    rider = _rider(user_model, "ana", complete=False)
    client.force_login(rider)

    response = client.post(reverse("events:squad_invite", args=[squad.invite_token]), follow=True)

    assert not EventSignup.objects.filter(event=squad.event, user=rider).exists()
    assert not SquadMember.objects.filter(squad=squad, user=rider).exists()
    assert _messages(response) == ["You can't join Alpha yet: this event requires a complete profile."]


@pytest.mark.django_db
def test_an_invite_link_does_not_reactivate_a_withdrawn_signup_that_fails(client, user_model):
    """Re-activating a withdrawn signup is signing up again, so it is held to the requirements."""
    squad = _squad(_event())
    rider = _rider(user_model, "ana", complete=False)
    EventSignup.objects.create(event=squad.event, user=rider, status=EventSignup.Status.WITHDRAWN)
    client.force_login(rider)

    client.post(reverse("events:squad_invite", args=[squad.invite_token]))

    assert EventSignup.objects.get(event=squad.event, user=rider).status == EventSignup.Status.WITHDRAWN


@pytest.mark.django_db
def test_a_rider_already_on_the_event_can_still_join_a_squad(client, user_model):
    """They joined the event already; the invite only adds a squad place."""
    squad = _squad(_event())
    rider = _rider(user_model, "ana", complete=False)
    EventSignup.objects.create(event=squad.event, user=rider)
    client.force_login(rider)

    client.post(reverse("events:squad_invite", args=[squad.invite_token]))

    assert SquadMember.objects.filter(squad=squad, user=rider, status=SquadMember.Status.MEMBER).exists()


@pytest.mark.django_db
def test_the_invite_page_offers_join_to_a_rider_already_on_the_event(client, user_model):
    """The page matches the exemption: no "Before you can join", and the Join button is there."""
    squad = _squad(_event())
    rider = _rider(user_model, "ana", complete=False)
    EventSignup.objects.create(event=squad.event, user=rider)
    client.force_login(rider)

    body = client.get(reverse("events:squad_invite", args=[squad.invite_token])).content.decode()

    assert "Before you can join" not in body
    assert "Join Squad" in body


@pytest.mark.django_db
def test_an_eligible_rider_joins_through_the_invite(client, user_model, complete_profile):
    """The usual path still works."""
    squad = _squad(_event())
    rider = _rider(user_model, "ana", complete_profile=complete_profile)
    client.force_login(rider)

    client.post(reverse("events:squad_invite", args=[squad.invite_token]))

    assert EventSignup.objects.filter(event=squad.event, user=rider, status=EventSignup.Status.REGISTERED).exists()
    assert SquadMember.objects.filter(squad=squad, user=rider).exists()


# --- a captain or admin adding members ---------------------------------------------------


@pytest.mark.django_db
def test_adding_members_skips_riders_who_do_not_qualify(client, app_admin, user_model, complete_profile):
    """A captain adding riders is held to the same requirements, and told who was left out and why."""
    event = _event(require_race_verified_signup=True)
    ready = _rider(user_model, "ready", race_ready=True, complete_profile=complete_profile)
    unverified = _rider(user_model, "unverified", complete_profile=complete_profile)
    incomplete = _rider(user_model, "incomplete", complete=False, race_ready=True)
    client.force_login(app_admin)

    response = client.post(
        reverse("events:add_members", args=[event.pk]),
        {"user_ids": [ready.pk, unverified.pk, incomplete.pk]},
        follow=True,
    )

    assert list(EventSignup.objects.filter(event=event).values_list("user__username", flat=True)) == ["ready"]
    assert _messages(response) == [
        "Added 1 member to the event.",
        "Not added -- the event's signup requirements aren't met: "
        "Unverified Rider (not Race Verified), Incomplete (incomplete profile).",
    ]


@pytest.mark.django_db
def test_adding_only_riders_who_do_not_qualify_says_so_without_a_success(client, app_admin, user_model):
    """No "Added 0 members" alongside the explanation."""
    event = _event()
    incomplete = _rider(user_model, "incomplete", complete=False)
    client.force_login(app_admin)

    response = client.post(reverse("events:add_members", args=[event.pk]), {"user_ids": [incomplete.pk]}, follow=True)

    assert not EventSignup.objects.filter(event=event).exists()
    assert _messages(response) == [
        "Not added -- the event's signup requirements aren't met: Incomplete (incomplete profile)."
    ]


@pytest.mark.django_db
def test_the_member_search_says_who_cannot_be_added(client, app_admin, user_model, complete_profile):
    """So the captain knows before picking, rather than after pressing Add."""
    event = _event()
    _rider(user_model, "anna", complete_profile=complete_profile)
    _rider(user_model, "annabel", complete=False)
    client.force_login(app_admin)

    results = client.get(reverse("events:add_members_search", args=[event.pk]), {"q": "anna"}).json()["results"]

    assert {r["display_name"]: r["blocked"] for r in results} == {"Anna Rider": "", "Annabel": "incomplete profile"}


@pytest.mark.django_db
def test_the_member_search_results_are_escaped_before_rendering(client, app_admin):
    """Names are chosen by riders: they must never reach innerHTML as markup."""
    event = _event()
    client.force_login(app_admin)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()
    script = body[body.index("function renderResults") : body.index("function updateSelected")]

    for field in ("u.display_name", "u.discord_username", "u.blocked"):
        assert re.search(rf"\+ {re.escape(field)} \+", script) is None, f"{field} goes into the HTML unescaped"
        assert f"escapeHtml({field})" in script


# --- the event form -----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_event_form_offers_both_settings_with_their_defaults(client, event_admin):
    """A new event starts with the profile requirement ticked and Race Verified not."""
    client.force_login(event_admin)

    body = client.get(reverse("events:event_create")).content.decode()

    profile_box = re.search(r'<input[^>]*name="require_complete_profile_signup"[^>]*>', body).group(0)
    race_box = re.search(r'<input[^>]*name="require_race_verified_signup"[^>]*>', body).group(0)
    assert "checked" in profile_box
    assert "checked" not in race_box
    assert "Require a complete profile to sign up" in body
    assert "Require Race Verified status to sign up" in body
