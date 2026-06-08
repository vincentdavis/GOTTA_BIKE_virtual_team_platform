"""Tests for events app."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from apps.events.models import Event, EventSignup
from apps.events.tasks import _format_signup_message, post_signup_notification


@pytest.fixture
def event(db) -> Event:
    today = date.today()
    return Event.objects.create(
        title="Friday Crit",
        start_date=today,
        end_date=today + timedelta(days=1),
        visible=True,
        signups_open=True,
    )


@pytest.fixture
def event_with_channel(db) -> Event:
    today = date.today()
    return Event.objects.create(
        title="Friday Crit",
        start_date=today,
        end_date=today + timedelta(days=1),
        visible=True,
        signups_open=True,
        signup_notification_channel_id=1234567890,
    )


def _make_signup(event: Event, user, **kwargs) -> EventSignup:
    return EventSignup.objects.create(event=event, user=user, **kwargs)


@pytest.mark.django_db
def test_format_message_includes_rider_name_and_event_title(event_with_channel, user) -> None:
    user.first_name = "Aria"
    user.last_name = "Tester"
    user.save(update_fields=["first_name", "last_name"])
    signup = _make_signup(event_with_channel, user)
    msg = _format_signup_message(signup, profile_url="https://example.test/u/1/")
    assert "Friday Crit" in msg
    assert "Aria Tester" in msg
    assert "https://example.test/u/1/" in msg


@pytest.mark.django_db
def test_format_message_includes_zp_category(event_with_channel, user, zp_team_rider_factory) -> None:
    user.zwid = 444001
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)  # Cat B
    signup = _make_signup(event_with_channel, user)
    msg = _format_signup_message(signup, profile_url=None)
    assert "ZP: **B**" in msg


@pytest.mark.django_db
def test_format_message_omits_timezone_when_not_required(event_with_channel, user) -> None:
    signup = _make_signup(event_with_channel, user, signup_timezone=["US/Eastern"])
    msg = _format_signup_message(signup, profile_url=None)
    assert "Timezone:" not in msg


@pytest.mark.django_db
def test_format_message_includes_timezone_when_required(event_with_channel, user) -> None:
    event_with_channel.timezone_required = True
    event_with_channel.save(update_fields=["timezone_required"])
    signup = _make_signup(event_with_channel, user, signup_timezone=["US/Eastern", "Europe/London"])
    msg = _format_signup_message(signup, profile_url=None)
    assert "Timezone: US/Eastern, Europe/London" in msg


@pytest.mark.django_db
def test_format_message_includes_squad_gender_when_required(event_with_channel, user) -> None:
    event_with_channel.squad_gender_required = True
    event_with_channel.save(update_fields=["squad_gender_required"])
    signup = _make_signup(event_with_channel, user, signup_squad_gender=["Male", "COED"])
    msg = _format_signup_message(signup, profile_url=None)
    assert "Squad Gender: Male, COED" in msg


@pytest.mark.django_db
def test_notify_task_skips_when_channel_unconfigured(event, user) -> None:
    signup = _make_signup(event, user)
    with patch("apps.events.tasks.send_discord_channel_message") as mock_send:
        result = post_signup_notification(signup_id=signup.pk)
    assert result["status"] == "skipped"
    assert mock_send.call_count == 0


@pytest.mark.django_db
def test_notify_task_posts_when_channel_configured(event_with_channel, user) -> None:
    signup = _make_signup(event_with_channel, user)
    with patch("apps.events.tasks.send_discord_channel_message", return_value=True) as mock_send:
        result = post_signup_notification(signup_id=signup.pk, profile_url="https://x/")
    assert result["status"] == "posted"
    assert mock_send.call_count == 1
    args, _ = mock_send.call_args
    assert args[0] == event_with_channel.signup_notification_channel_id
    assert "Friday Crit" in args[1]


@pytest.mark.django_db
def test_notify_task_returns_error_for_unknown_signup() -> None:
    with patch("apps.events.tasks.send_discord_channel_message") as mock_send:
        result = post_signup_notification(signup_id=999999)
    assert result["status"] == "error"
    assert mock_send.call_count == 0


@pytest.mark.django_db
def test_notify_task_returns_error_when_send_fails(event_with_channel, user) -> None:
    signup = _make_signup(event_with_channel, user)
    with patch("apps.events.tasks.send_discord_channel_message", return_value=False):
        result = post_signup_notification(signup_id=signup.pk)
    assert result["status"] == "error"
    assert result["reason"] == "send_failed"


# --- Template / page smoke tests -------------------------------------------------
# These guard against template syntax errors and view/template integration
# breakage, which the unit tests above don't exercise (templates are only parsed
# when actually rendered).

EVENTS_TEMPLATES = [
    "events/my_events.html",
    "events/availability_results.html",
    "events/event_detail.html",
    "events/event_form.html",
    "events/squad_form.html",
    "events/squad_manage.html",
    "events/_squad_panel.html",
    "events/event_all_races.html",
]


@pytest.mark.parametrize("template_name", EVENTS_TEMPLATES)
def test_events_templates_compile(template_name: str) -> None:
    from django.template.loader import get_template

    get_template(template_name)  # raises TemplateSyntaxError on a bad block tag


@pytest.mark.django_db
def test_my_events_page_renders_empty(auth_client) -> None:
    from django.urls import reverse

    response = auth_client.get(reverse("events:my_events"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_my_events_renders_scheduled_race_with_calendar_links(auth_client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import (
        AvailabilityGrid,
        AvailabilitySlotSelection,
        Squad,
        SquadMember,
    )

    today = date.today()
    event = Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=7),
        visible=True,
    )
    EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)
    squad = Squad.objects.create(event=event, name="Squad A")
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today,
        end_date=today + timedelta(days=7),
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    selection = AvailabilitySlotSelection.objects.create(
        grid=grid,
        name="Race 1",
        slot_date=today + timedelta(days=3),
        slot_time="18:30",
        status=AvailabilitySlotSelection.Status.CONFIRMED,
    )
    selection.selected_users.add(team_member)

    response = auth_client.get(reverse("events:my_events"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Download .ics" in body
    assert "/events/race/" in body
    assert "calendar.google.com" in body
