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
