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
    "events/squad_availability.html",
    "events/availability_builder.html",
    "events/_squad_panel.html",
    "events/_squad_manage_panel.html",
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


@pytest.mark.django_db
def test_squad_manage_renders_riders_section(client, event_admin, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(event_admin)
    today = date.today()
    event = Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=7),
        visible=True,
    )
    squad = Squad.objects.create(event=event, name="Squad A")
    SquadMember.objects.create(squad=squad, user=event_admin, status=SquadMember.Status.MEMBER)
    # team_member is signed up but not in the squad -> should be an "add rider" option
    EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    response = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Riders" in body
    assert f'id="squad-panel-{squad.pk}"' in body
    assert "+ Add rider" in body


@pytest.mark.django_db
def test_squad_assign_from_manage_page_returns_panel(client, event_admin, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(event_admin)
    today = date.today()
    event = Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=7),
        visible=True,
    )
    squad = Squad.objects.create(event=event, name="Squad A")
    signup = EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    manage_url = reverse("events:squad_manage", args=[event.pk])
    response = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=f"http://testserver{manage_url}",
    )
    assert response.status_code == 200
    assert f'id="squad-panel-{squad.pk}"' in response.content.decode()
    assert SquadMember.objects.filter(squad=squad, user=team_member).exists()


# ---- ZR category enforcement on squad join ----


@pytest.mark.parametrize(
    ("rider_cat", "expected_ok"),
    [
        ("Diamond", False),  # stronger than max (Sapphire)
        ("Emerald", False),  # stronger than max
        ("Sapphire", True),  # at max
        ("Platinum", True),  # inside band
        ("Gold", True),  # at min
        ("Silver", False),  # weaker than min (Gold)
        ("Copper", False),  # weaker than min
        ("", False),  # no ZR category on record
    ],
)
def test_squad_check_zr_eligibility_band(rider_cat, expected_ok) -> None:
    from apps.events.models import Squad

    squad = Squad(
        min_zwift_racing_category="Gold",
        max_zwift_racing_category="Sapphire",
        enforce_min_zwift_racing_category=True,
        enforce_max_zwift_racing_category=True,
    )
    ok, reason = squad.check_zr_eligibility(rider_cat)
    assert ok is expected_ok
    if not ok:
        assert reason  # a human-readable reason is always provided when blocked


def test_squad_check_zr_eligibility_respects_enforce_flags() -> None:
    from apps.events.models import Squad

    # Bounds set but neither checkbox enabled -> never blocks.
    squad = Squad(min_zwift_racing_category="Gold", max_zwift_racing_category="Sapphire")
    assert squad.check_zr_eligibility("Copper") == (True, "")

    # Only the max bound enforced -> too-strong blocked, anything weaker allowed.
    squad_max = Squad(max_zwift_racing_category="Sapphire", enforce_max_zwift_racing_category=True)
    assert squad_max.check_zr_eligibility("Diamond")[0] is False
    assert squad_max.check_zr_eligibility("Copper")[0] is True


@pytest.mark.django_db
def test_squad_assign_blocked_by_zr_enforcement(client, event_admin, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember
    from apps.zwiftracing.models import ZRRider

    client.force_login(event_admin)
    team_member.zwid = 555001
    team_member.save(update_fields=["zwid"])
    ZRRider.objects.create(zwid=team_member.zwid, race_current_category="Copper")

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(
        event=event,
        name="Squad A",
        min_zwift_racing_category="Gold",
        enforce_min_zwift_racing_category=True,
    )
    signup = EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    response = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
        HTTP_HX_REQUEST="true",
    )
    # Blocked: no DOM swap (204), toast fired, and no membership created.
    assert response.status_code == 204
    assert "showToast" in response.headers.get("HX-Trigger", "")
    assert not SquadMember.objects.filter(squad=squad, user=team_member).exists()


# ---- Squad gender enforcement ----


@pytest.mark.parametrize(
    ("squad_gender", "user_gender", "expected_ok"),
    [
        ("Male", "male", True),
        ("Male", "female", False),
        ("Male", "other", False),
        ("Male", "", False),
        ("Female", "female", True),
        ("Female", "male", False),
        ("COED", "male", True),
        ("COED", "female", True),
        ("COED", "", True),
    ],
)
def test_squad_check_gender_eligibility(squad_gender, user_gender, expected_ok) -> None:
    from apps.events.models import Squad

    squad = Squad(gender=squad_gender, enforce_gender=True)
    ok, reason = squad.check_gender_eligibility(user_gender)
    assert ok is expected_ok
    if not ok:
        assert reason


def test_squad_check_gender_eligibility_respects_enforce_flag() -> None:
    from apps.events.models import Squad

    # enforce off -> never blocks even on mismatch
    assert Squad(gender="Male").check_gender_eligibility("female") == (True, "")
    # gender unset on squad -> nothing to enforce
    assert Squad(gender="", enforce_gender=True).check_gender_eligibility("female") == (True, "")


@pytest.mark.django_db
def test_squad_assign_blocked_by_gender_enforcement(client, event_admin, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(event_admin)
    team_member.gender = "female"
    team_member.save(update_fields=["gender"])

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(event=event, name="Men's A", gender="Male", enforce_gender=True)
    signup = EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    response = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204
    assert "showToast" in response.headers.get("HX-Trigger", "")
    assert not SquadMember.objects.filter(squad=squad, user=team_member).exists()


@pytest.mark.django_db
def test_squad_form_requires_gender(event_admin) -> None:
    from apps.events.forms import SquadForm

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    # Missing gender -> invalid
    form = SquadForm(data={"name": "No Gender"}, event_prefixes=event.prefixes or [])
    assert not form.is_valid()
    assert "gender" in form.errors
    # Valid gender -> gender error cleared
    form_ok = SquadForm(data={"name": "Squad A", "gender": "COED"}, event_prefixes=event.prefixes or [])
    form_ok.is_valid()
    assert "gender" not in form_ok.errors


# ---- Availability grid templates ----


def _avail_event_squad():
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=14), visible=True
    )
    from apps.events.models import Squad

    squad = Squad.objects.create(event=event, name="Squad A", gender="COED")
    return event, squad


@pytest.mark.django_db
def test_availability_template_create(client, event_admin) -> None:
    import json

    from django.urls import reverse

    from apps.events.models import AvailabilityGridTemplate

    client.force_login(event_admin)
    event, squad = _avail_event_squad()

    payload = {
        "name": "Weeknights EU",
        "start_time": "19:00",
        "end_time": "21:00",
        "timezone": "UTC",
        "slot_duration": 30,
        "length_days": 7,
        "max_races_question": True,
        "rest_days_question": False,
    }
    response = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200
    t = AvailabilityGridTemplate.objects.get(squad=squad)
    assert t.name == "Weeknights EU"
    assert t.slot_duration == 30
    assert t.default_length_days == 7
    assert t.max_races_question is True
    assert t.rest_days_question is False


@pytest.mark.django_db
def test_availability_template_create_requires_name(client, event_admin) -> None:
    import json

    from django.urls import reverse

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    response = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        data=json.dumps({"start_time": "19:00", "end_time": "21:00", "slot_duration": 30, "length_days": 7}),
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_availability_template_create_denied_for_non_manager(client, team_member) -> None:
    import json

    from django.urls import reverse

    client.force_login(team_member)
    event, squad = _avail_event_squad()
    body = {"name": "X", "start_time": "19:00", "end_time": "21:00", "slot_duration": 30, "length_days": 7}
    response = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        data=json.dumps(body),
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_availability_template_apply_creates_draft(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGrid, AvailabilityGridTemplate

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    template = AvailabilityGridTemplate.objects.create(
        squad=squad,
        name="UTC 7-day",
        start_time="18:00",
        end_time="20:00",
        grid_timezone="UTC",
        slot_duration=30,
        default_length_days=7,
        max_races_question=True,
        rest_days_question=True,
    )
    response = client.post(
        reverse("events:availability_template_apply", args=[event.pk, squad.pk, template.pk]),
        {"start_date": "2026-07-01"},
    )
    assert response.status_code == 302
    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.status == AvailabilityGrid.Status.DRAFT
    assert grid.start_date.isoformat() == "2026-07-01"
    # default_length_days=7 -> end is start + 6 days
    assert grid.end_date.isoformat() == "2026-07-07"
    assert grid.start_time == "18:00"  # UTC template, no conversion
    assert grid.slot_duration == 30
    assert grid.blocked_cells == []
    assert grid.max_races_question is True
    assert grid.rest_days_question is True


@pytest.mark.django_db
def test_availability_template_apply_converts_local_to_utc(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGrid, AvailabilityGridTemplate

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    # New York 19:00 in July (EDT, UTC-4) -> 23:00 UTC
    template = AvailabilityGridTemplate.objects.create(
        squad=squad,
        name="NY evenings",
        start_time="19:00",
        end_time="21:00",
        grid_timezone="America/New_York",
        slot_duration=30,
        default_length_days=1,
    )
    response = client.post(
        reverse("events:availability_template_apply", args=[event.pk, squad.pk, template.pk]),
        {"start_date": "2026-07-01"},
    )
    assert response.status_code == 302
    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.start_time == "23:00"  # converted from local 19:00 EDT
    assert grid.grid_timezone == "America/New_York"


@pytest.mark.django_db
def test_availability_template_delete(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGridTemplate

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    template = AvailabilityGridTemplate.objects.create(
        squad=squad, name="Tmp", start_time="18:00", end_time="20:00", slot_duration=30
    )
    response = client.post(
        reverse("events:availability_template_delete", args=[event.pk, squad.pk, template.pk])
    )
    assert response.status_code == 302
    assert not AvailabilityGridTemplate.objects.filter(pk=template.pk).exists()


# ---- Edit draft availability grids ----


def _draft_grid(squad, **overrides):
    from apps.events.models import AvailabilityGrid

    fields = {
        "squad": squad,
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 7),
        "start_time": "18:00",
        "end_time": "20:00",
        "slot_duration": 30,
        "grid_timezone": "UTC",
        "blocked_cells": [],
        "status": AvailabilityGrid.Status.DRAFT,
    }
    fields.update(overrides)
    return AvailabilityGrid.objects.create(**fields)


@pytest.mark.django_db
def test_availability_edit_get_prefills_draft(client, event_admin) -> None:
    from django.urls import reverse

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    grid = _draft_grid(squad)

    response = client.get(reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Edit Availability Grid" in body
    assert '"start_time": "18:00"' in body  # INITIAL_GRID embedded


@pytest.mark.django_db
def test_availability_edit_blocked_for_published(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGrid

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    grid = _draft_grid(squad, status=AvailabilityGrid.Status.PUBLISHED)

    response = client.get(reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]))
    assert response.status_code == 302  # redirected, not editable


@pytest.mark.django_db
def test_availability_edit_post_updates_grid(client, event_admin) -> None:
    import json

    from django.urls import reverse

    from apps.events.models import AvailabilityGrid

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    grid = _draft_grid(squad, title="Original")

    payload = {
        "title": "Renamed",
        "start_date": "2026-07-01",
        "end_date": "2026-07-07",
        "start_time": "19:00",
        "end_time": "21:00",
        "slot_duration": 30,
        "timezone": "UTC",
        "blocked_cells": [],
        "expires": None,
        "max_races_question": True,
        "rest_days_question": False,
    }
    response = client.post(
        reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200
    # No new grid created; the existing one is updated in place.
    assert AvailabilityGrid.objects.filter(squad=squad).count() == 1
    grid.refresh_from_db()
    assert grid.title == "Renamed"
    assert grid.start_time == "19:00"
    assert grid.max_races_question is True
    assert grid.status == AvailabilityGrid.Status.DRAFT


@pytest.mark.django_db
def test_availability_edit_denied_for_non_manager(client, team_member) -> None:
    from django.urls import reverse

    client.force_login(team_member)
    event, squad = _avail_event_squad()
    grid = _draft_grid(squad)

    response = client.get(reverse("events:availability_edit", args=[event.pk, squad.pk, grid.id]))
    assert response.status_code == 302  # redirected to event detail, no permission


@pytest.mark.parametrize(
    ("sd", "ed", "st", "et", "tz"),
    [
        (date(2026, 7, 1), date(2026, 7, 7), "08:00", "20:00", "America/New_York"),
        (date(2026, 1, 5), date(2026, 1, 11), "19:00", "21:00", "Europe/London"),
        (date(2026, 7, 1), date(2026, 7, 1), "22:00", "23:30", "America/Los_Angeles"),  # crosses UTC midnight
        (date(2026, 3, 8), date(2026, 3, 14), "07:00", "09:00", "America/New_York"),  # DST start week
        (date(2026, 7, 1), date(2026, 7, 7), "08:00", "20:00", "UTC"),
    ],
)
def test_convert_utc_to_local_config_round_trips(sd, ed, st, et, tz) -> None:
    from apps.events.tz_utils import convert_local_to_utc, convert_utc_to_local_config

    u = convert_local_to_utc(sd, ed, st, et, tz)
    back = convert_utc_to_local_config(u[0], u[1], u[2], u[3], tz)
    assert back == (sd, ed, st, et)


# ---- Hide days with no availability ----


def test_drop_fully_blocked_days() -> None:
    from apps.events.tz_utils import convert_grid_to_local, drop_fully_blocked_days

    dates = ["2026-06-15", "2026-06-16"]
    slots = ["18:00", "18:30", "19:00", "19:30"]
    blocked = [{"date": "2026-06-15", "time": t} for t in slots]  # all of Jun 15 blocked
    grid_data = convert_grid_to_local(dates, "18:00", "20:00", 30, blocked, "UTC")
    assert "2026-06-15" in grid_data["display_dates"]

    drop_fully_blocked_days(grid_data)
    assert grid_data["display_dates"] == ["2026-06-16"]


def _published_grid(squad, *, hide_empty_days, **overrides):
    from apps.events.models import AvailabilityGrid

    slots = ["18:00", "18:30", "19:00", "19:30"]
    fields = {
        "squad": squad,
        "start_date": date(2026, 6, 15),
        "end_date": date(2026, 6, 16),
        "start_time": "18:00",
        "end_time": "20:00",
        "slot_duration": 30,
        "grid_timezone": "UTC",
        "blocked_cells": [{"date": "2026-06-15", "time": t} for t in slots],  # Jun 15 fully blocked
        "status": AvailabilityGrid.Status.PUBLISHED,
        "hide_empty_days": hide_empty_days,
    }
    fields.update(overrides)
    return AvailabilityGrid.objects.create(**fields)


@pytest.mark.django_db
def test_respond_hides_fully_blocked_day_when_flag_on(auth_client) -> None:
    from django.urls import reverse

    event, squad = _avail_event_squad()
    grid = _published_grid(squad, hide_empty_days=True)

    response = auth_client.get(reverse("events:availability_respond", args=[event.pk, squad.pk, grid.id]))
    assert response.status_code == 200
    assert 'var dates = ["2026-06-16"];' in response.content.decode()  # Jun 15 dropped


@pytest.mark.django_db
def test_respond_shows_all_days_when_flag_off(auth_client) -> None:
    from django.urls import reverse

    event, squad = _avail_event_squad()
    grid = _published_grid(squad, hide_empty_days=False)

    response = auth_client.get(reverse("events:availability_respond", args=[event.pk, squad.pk, grid.id]))
    assert response.status_code == 200
    assert 'var dates = ["2026-06-15", "2026-06-16"];' in response.content.decode()


@pytest.mark.django_db
def test_availability_save_stores_hide_empty_days(client, event_admin) -> None:
    import json

    from django.urls import reverse

    from apps.events.models import AvailabilityGrid

    client.force_login(event_admin)
    event, squad = _avail_event_squad()
    payload = {
        "title": "",
        "start_date": "2026-06-15",
        "end_date": "2026-06-16",
        "start_time": "18:00",
        "end_time": "20:00",
        "slot_duration": 30,
        "timezone": "UTC",
        "blocked_cells": [],
        "expires": None,
        "max_races_question": False,
        "rest_days_question": False,
        "hide_empty_days": True,
    }
    response = client.post(
        reverse("events:availability_create", args=[event.pk, squad.pk]),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200
    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.hide_empty_days is True
