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
    "events/squad_v_report.html",
    "events/_eligibility_table.html",
    "events/_filter_select_script.html",
    "events/squad_availability.html",
    "events/availability_builder.html",
    "events/_slot_selections_container.html",
    "events/_slot_ds_list.html",
    "events/_slot_ds_results.html",
    "events/_squad_panel.html",
    "events/_squad_manage_panel.html",
    "events/event_all_races.html",
    "events/all_scheduled_races.html",
    "events/_scheduled_race_card.html",
    "events/_participation_report.html",
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
def test_all_scheduled_races_page_renders_empty(auth_client) -> None:
    from django.urls import reverse

    response = auth_client.get(reverse("events:all_races"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_all_scheduled_races_lists_races_across_events(auth_client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGrid, AvailabilitySlotSelection, Squad

    today = date.today()

    def _make_race(event_title: str, squad_name: str, race_name: str) -> None:
        event = Event.objects.create(
            title=event_title,
            start_date=today,
            end_date=today + timedelta(days=7),
            visible=True,
        )
        squad = Squad.objects.create(event=event, name=squad_name)
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
            name=race_name,
            slot_date=today + timedelta(days=3),
            slot_time="18:30",
            status=AvailabilitySlotSelection.Status.CONFIRMED,
        )
        selection.selected_users.add(team_member)

    _make_race("ZRL Spring", "Squad A", "Race 1")
    _make_race("WTRL TTT", "Squad B", "Race 2")

    response = auth_client.get(reverse("events:all_races"))
    assert response.status_code == 200
    body = response.content.decode()
    # Both events and their races appear on the single cross-event page.
    assert "ZRL Spring" in body
    assert "WTRL TTT" in body
    assert "Race 1" in body
    assert "Race 2" in body


@pytest.mark.django_db
def test_all_scheduled_races_hides_event_invite_but_keeps_course_and_thread(auth_client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGrid, AvailabilitySlotSelection, Squad

    today = date.today()
    event = Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=7),
        visible=True,
    )
    squad = Squad.objects.create(event=event, name="Squad A")
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
        event_invite_url="https://zwift.test/invite",
        course_url="https://zwift.test/course",
        thread_link="https://discord.test/thread",
    )
    selection.selected_users.add(team_member)

    # Cross-event page: no "Event invite" link, but Course and Discord thread remain.
    all_races = auth_client.get(reverse("events:all_races")).content.decode()
    assert "Event invite" not in all_races
    assert "https://zwift.test/invite" not in all_races
    assert "Course" in all_races
    assert "Discord thread" in all_races

    # Per-event page still shows the invite link.
    per_event = auth_client.get(reverse("events:event_all_races", args=[event.pk])).content.decode()
    assert "Event invite" in per_event


@pytest.mark.django_db
def test_all_scheduled_races_excludes_past_races(auth_client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import AvailabilityGrid, AvailabilitySlotSelection, Squad

    today = date.today()
    event = Event.objects.create(
        title="Past Event",
        start_date=today - timedelta(days=14),
        end_date=today,
        visible=True,
    )
    squad = Squad.objects.create(event=event, name="Squad A")
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today - timedelta(days=14),
        end_date=today,
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    AvailabilitySlotSelection.objects.create(
        grid=grid,
        name="Old Race",
        slot_date=today - timedelta(days=5),
        slot_time="18:30",
        status=AvailabilitySlotSelection.Status.CONFIRMED,
    )

    response = auth_client.get(reverse("events:all_races"))
    assert response.status_code == 200
    assert "Old Race" not in response.content.decode()


@pytest.mark.django_db
def test_all_scheduled_races_requires_team_member(client, user) -> None:
    from django.urls import reverse

    client.force_login(user)  # plain user, no team_member permission
    response = client.get(reverse("events:all_races"))
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_racing_report_tallies_past_and_future_races(client, event_admin, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import (
        AvailabilityGrid,
        AvailabilitySlotSelection,
        Squad,
        SquadMember,
    )

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=14), visible=True
    )
    squad = Squad.objects.create(event=event, name="Squad A")
    SquadMember.objects.create(squad=squad, user=team_member, status=SquadMember.Status.MEMBER)
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today - timedelta(days=14),
        end_date=today + timedelta(days=14),
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        status=AvailabilityGrid.Status.PUBLISHED,
    )

    def _race(name: str, day_offset: int) -> AvailabilitySlotSelection:
        sel = AvailabilitySlotSelection.objects.create(
            grid=grid, name=name, slot_date=today + timedelta(days=day_offset), slot_time="18:30"
        )
        sel.selected_users.add(team_member)
        return sel

    _race("Past 1", -10)
    _race("Past 2", -3)
    _race("Future 1", 5)

    client.force_login(event_admin)
    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")
    assert resp.status_code == 200
    assert resp.context["active_tab"] == "participation"

    participation = resp.context["participation"]
    # One squad group, one rider row with 2 raced + 1 upcoming.
    group = next(g for g in participation if g["squad"].pk == squad.pk)
    row = next(r for r in group["rows"] if r["user"].pk == team_member.pk)
    assert row["raced_count"] == 2
    assert row["upcoming_count"] == 1
    assert row["upcoming"][0]["name"] == "Future 1"
    body = resp.content.decode()
    assert "Future 1" in body
    # Riders render via the shared user tooltip component (avatar/icon + hover).
    assert "dropdown dropdown-hover" in body
    # Both tabs are present on the page.
    assert 'aria-label="All Races"' in body
    assert 'aria-label="Participation"' in body


@pytest.mark.django_db
def test_participation_tab_visible_to_any_team_member(auth_client) -> None:
    from django.urls import reverse

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    # auth_client is a plain team_member (no squad-manage permission) and can still view.
    resp = auth_client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")
    assert resp.status_code == 200
    assert resp.context["active_tab"] == "participation"
    assert 'aria-label="Participation"' in resp.content.decode()


@pytest.mark.django_db
def test_squad_manage_shows_participation_button(client, event_admin) -> None:
    from django.urls import reverse

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()
    assert "Participation" in body
    assert reverse("events:event_all_races", args=[event.pk]) + "?tab=participation" in body


@pytest.mark.django_db
def test_squad_captain_can_view_manage_page_with_own_controls_only(client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad_a = Squad.objects.create(event=event, name="Squad A")
    squad_b = Squad.objects.create(event=event, name="Squad B")
    squad_a.captains.add(team_member)  # captain of A only

    client.force_login(team_member)
    resp = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert resp.status_code == 200
    assert resp.context["can_manage_all"] is False
    body = resp.content.decode()
    # Edit shows for their own squad, not for the other squad.
    assert reverse("events:squad_edit", args=[event.pk, squad_a.pk]) in body
    assert reverse("events:squad_edit", args=[event.pk, squad_b.pk]) not in body
    # Manager-only global actions are hidden.
    assert reverse("events:squad_create", args=[event.pk]) not in body
    assert reverse("events:squad_assign_page", args=[event.pk]) not in body


@pytest.mark.django_db
def test_non_captain_team_member_cannot_view_manage_page(client, team_member, user) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(event=event, name="Squad A")
    squad.captains.add(user)  # someone else leads it

    client.force_login(team_member)  # not a leader of any squad
    resp = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert resp.status_code == 302


@pytest.mark.django_db
def test_squad_captain_can_edit_own_squad_only(client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad_a = Squad.objects.create(event=event, name="Squad A")
    squad_b = Squad.objects.create(event=event, name="Squad B")
    squad_a.captains.add(team_member)

    client.force_login(team_member)
    assert client.get(reverse("events:squad_edit", args=[event.pk, squad_a.pk])).status_code == 200
    # Not a leader of squad B -> redirected away.
    assert client.get(reverse("events:squad_edit", args=[event.pk, squad_b.pk])).status_code == 302


@pytest.mark.django_db
def test_squad_captain_can_generate_invite_for_own_squad_only(client, team_member) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad_a = Squad.objects.create(event=event, name="Squad A")
    squad_b = Squad.objects.create(event=event, name="Squad B")
    squad_a.captains.add(team_member)

    client.force_login(team_member)
    client.post(reverse("events:squad_regenerate_token", args=[event.pk, squad_a.pk]))
    client.post(reverse("events:squad_regenerate_token", args=[event.pk, squad_b.pk]))

    squad_a.refresh_from_db()
    squad_b.refresh_from_db()
    assert squad_a.invite_token  # generated for own squad
    assert not squad_b.invite_token  # blocked for the other squad


@pytest.mark.django_db
def test_event_admin_keeps_full_squad_manage_access(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(event=event, name="Squad A")

    client.force_login(event_admin)
    resp = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert resp.status_code == 200
    assert resp.context["can_manage_all"] is True
    body = resp.content.decode()
    assert reverse("events:squad_create", args=[event.pk]) in body  # Add Squad
    assert reverse("events:squad_edit", args=[event.pk, squad.pk]) in body


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
def test_squad_manage_has_search_and_sorts_by_name(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    client.force_login(event_admin)
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True)
    # Created out of order; the page should list them alphabetically by name.
    Squad.objects.create(event=event, name="Charlie")
    Squad.objects.create(event=event, name="Alpha")
    Squad.objects.create(event=event, name="Bravo")

    body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()
    assert 'id="squad-search"' in body
    assert body.index("Alpha") < body.index("Bravo") < body.index("Charlie")


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


def test_squad_enforcement_summary() -> None:
    from apps.events.models import Squad

    squad = Squad(
        gender="Female",
        enforce_gender=True,
        min_zwift_category="D",
        max_zwift_category="B",
        enforce_min_zwift_category=True,
        enforce_max_zwift_category=True,
        min_zwift_racing_category="Gold",
        enforce_min_zwift_racing_category=True,
    )
    summary = squad.enforcement_summary
    assert "Gender: Female" in summary
    assert "Zwift: B-D" in summary
    assert "ZR: Gold or stronger" in summary
    # A bound set without its enforce flag is excluded.
    assert not any("Women's" in s for s in summary)
    # Nothing enforced -> empty.
    assert Squad(gender="COED", min_zwift_category="A").enforcement_summary == []


@pytest.mark.django_db
def test_squad_manage_shows_enforcement_badges(client, event_admin) -> None:
    from django.urls import reverse

    from apps.events.models import Squad

    client.force_login(event_admin)
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    Squad.objects.create(
        event=event, name="Women A", gender="Female", enforce_gender=True,
        max_zwift_category="B", enforce_max_zwift_category=True,
    )
    response = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Enforced Requirements" in body
    assert "Gender: Female" in body
    assert "Zwift: B or weaker" in body


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


@pytest.mark.django_db
def test_squad_form_saves_womens_zwift_category(event_admin) -> None:
    from apps.events.forms import SquadForm

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    form = SquadForm(
        data={
            "name": "Squad A",
            "gender": "COED",
            "min_womens_zwift_category": "B",
            "max_womens_zwift_category": "A",
        },
        event_prefixes=event.prefixes or [],
    )
    assert form.is_valid(), form.errors
    squad = form.save(commit=False)
    squad.event = event
    squad.save()
    squad.refresh_from_db()
    assert squad.min_womens_zwift_category == "B"
    assert squad.max_womens_zwift_category == "A"


# ---- Women's Zwift category enforcement ----


@pytest.mark.parametrize(
    ("rider_cat", "expected_ok"),
    [
        ("A+", False),  # stronger than max (B)
        ("A", False),
        ("B", True),
        ("C", True),
        ("D", True),
        ("E", False),  # weaker than min (D)
        ("", True),  # no women's category -> not affected
    ],
)
def test_squad_womens_zwift_eligibility_band(rider_cat, expected_ok) -> None:
    from apps.events.models import Squad

    squad = Squad(
        min_womens_zwift_category="D",
        max_womens_zwift_category="B",
        enforce_min_womens_zwift_category=True,
        enforce_max_womens_zwift_category=True,
    )
    ok, reason = squad.check_womens_zwift_eligibility(rider_cat)
    assert ok is expected_ok
    if not ok:
        assert reason


def test_squad_womens_zwift_eligibility_respects_flags() -> None:
    from apps.events.models import Squad

    # Bound set but enforce off -> never blocks.
    assert Squad(min_womens_zwift_category="D").check_womens_zwift_eligibility("E") == (True, "")
    # Only max enforced -> too-strong blocked, weaker allowed.
    s = Squad(max_womens_zwift_category="B", enforce_max_womens_zwift_category=True)
    assert s.check_womens_zwift_eligibility("A")[0] is False
    assert s.check_womens_zwift_eligibility("C")[0] is True


@pytest.mark.django_db
def test_squad_assign_blocked_by_womens_zwift_enforcement(
    client, event_admin, team_member, zp_team_rider_factory
) -> None:
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(event_admin)
    team_member.zwid = 556001
    team_member.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=team_member.zwid, divw=50)  # women's category E

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(
        event=event,
        name="Women's A",
        gender="COED",
        min_womens_zwift_category="D",
        enforce_min_womens_zwift_category=True,
    )
    signup = EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    response = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204  # blocked: E is below the squad minimum (D)
    assert "showToast" in response.headers.get("HX-Trigger", "")
    assert not SquadMember.objects.filter(squad=squad, user=team_member).exists()


# ---- Zwift (overall) category enforcement ----


@pytest.mark.parametrize(
    ("rider_cat", "expected_ok"),
    [
        ("A+", False),  # stronger than max (B)
        ("A", False),
        ("B", True),
        ("C", True),
        ("D", True),
        ("E", False),  # weaker than min (D)
        ("", True),  # no Zwift category -> not affected
    ],
)
def test_squad_zwift_eligibility_band(rider_cat, expected_ok) -> None:
    from apps.events.models import Squad

    squad = Squad(
        min_zwift_category="D",
        max_zwift_category="B",
        enforce_min_zwift_category=True,
        enforce_max_zwift_category=True,
    )
    ok, reason = squad.check_zwift_eligibility(rider_cat)
    assert ok is expected_ok
    if not ok:
        assert reason


def test_squad_zwift_eligibility_respects_flags() -> None:
    from apps.events.models import Squad

    # Bound set but enforce off -> never blocks.
    assert Squad(min_zwift_category="D").check_zwift_eligibility("E") == (True, "")
    # Only max enforced -> too-strong blocked, weaker allowed.
    s = Squad(max_zwift_category="B", enforce_max_zwift_category=True)
    assert s.check_zwift_eligibility("A")[0] is False
    assert s.check_zwift_eligibility("C")[0] is True


@pytest.mark.django_db
def test_squad_assign_blocked_by_zwift_enforcement(
    client, event_admin, team_member, zp_team_rider_factory
) -> None:
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(event_admin)
    team_member.zwid = 557001
    team_member.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=team_member.zwid, div=10)  # overall category A

    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )
    squad = Squad.objects.create(
        event=event,
        name="B-and-weaker",
        gender="COED",
        max_zwift_category="B",
        enforce_max_zwift_category=True,
    )
    signup = EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)

    response = client.post(
        reverse("events:squad_assign", args=[event.pk]),
        {"signup_id": signup.pk, "squad_id": squad.pk},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204  # blocked: A is above the squad maximum (B)
    assert "showToast" in response.headers.get("HX-Trigger", "")
    assert not SquadMember.objects.filter(squad=squad, user=team_member).exists()


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


# ---- Directeur Sportif (DS) ----


def _ds_user(user_model, name="DS", discord_id="900001", roles=None):
    return user_model.objects.create(
        username=f"ds_{discord_id}",
        first_name=name,
        discord_id=discord_id,
        discord_username=name.lower(),
        discord_roles=roles or {},
    )


def _ds_squad_grid_slot(event, *, role_id=999, slot_date=None):
    from apps.events.models import AvailabilityGrid, AvailabilitySlotSelection, Squad

    squad = Squad.objects.create(event=event, name="Squad A", gender="COED", team_discord_role=role_id)
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    selection = AvailabilitySlotSelection.objects.create(
        grid=grid, name="Race 1", slot_date=slot_date or date(2026, 7, 3), slot_time="19:00"
    )
    return squad, grid, selection


@pytest.mark.django_db
def test_ds_assign_squad_role_only_when_not_held(user_model):
    from apps.events import ds_service
    from apps.events.models import Squad

    squad = Squad(name="S", team_discord_role=999)
    fresh = _ds_user(user_model, discord_id="900100", roles={})
    with patch("apps.events.ds_service.add_discord_role", return_value=True):
        assert ds_service.assign_squad_role(fresh, squad) is True  # newly assigned
    fresh.refresh_from_db()
    assert "999" in fresh.discord_roles

    already = _ds_user(user_model, discord_id="900101", roles={"999": "S"})
    with patch("apps.events.ds_service.add_discord_role", return_value=True) as m:
        assert ds_service.assign_squad_role(already, squad) is False  # already held; not ours
        assert m.call_count == 0


@pytest.mark.django_db
def test_ds_should_remove_respects_membership(user_model):
    today = date.today()
    event = Event.objects.create(title="E", start_date=today, end_date=today + timedelta(days=7), visible=True)
    squad, _grid, _sel = _ds_squad_grid_slot(event)
    from apps.events import ds_service
    from apps.events.models import SquadMember

    outsider = _ds_user(user_model, discord_id="900200")
    assert ds_service.should_remove_squad_role(outsider, squad, exclude_slot_ds_pk=None) is True

    member = _ds_user(user_model, discord_id="900201")
    SquadMember.objects.create(squad=squad, user=member, status=SquadMember.Status.MEMBER)
    assert ds_service.should_remove_squad_role(member, squad, exclude_slot_ds_pk=None) is False


@pytest.mark.django_db
def test_remove_expired_ds_roles_task():
    from apps.events.models import SlotDS
    from apps.events.tasks import remove_expired_ds_roles

    today = date.today()
    event = Event.objects.create(title="E", start_date=today, end_date=today + timedelta(days=7), visible=True)
    from django.contrib.auth import get_user_model

    um = get_user_model()
    # Past race, DS we assigned the role to, not otherwise entitled -> removed.
    _squad, _g, past_sel = _ds_squad_grid_slot(event, slot_date=date(2020, 1, 1))
    ds_user = _ds_user(um, discord_id="900300", roles={"999": "Squad A"})
    SlotDS.objects.create(selection=past_sel, user=ds_user, role_was_assigned=True)
    # Future race for a second DS -> skipped (not past).
    from apps.events.models import AvailabilitySlotSelection

    future_sel = AvailabilitySlotSelection.objects.create(
        grid=past_sel.grid, name="Future", slot_date=date(2999, 1, 1), slot_time="19:00"
    )
    future_user = _ds_user(um, discord_id="900301", roles={"999": "Squad A"})
    SlotDS.objects.create(selection=future_sel, user=future_user, role_was_assigned=True)

    with patch("apps.events.ds_service.remove_discord_role", return_value=True) as m:
        result = remove_expired_ds_roles.call()

    assert result["removed"] == 1
    assert m.call_count == 1
    past_ds = SlotDS.objects.get(selection=past_sel, user=ds_user)
    assert past_ds.role_removed_at is not None
    future_ds = SlotDS.objects.get(selection=future_sel, user=future_user)
    assert future_ds.role_removed_at is None  # future race untouched


@pytest.mark.django_db
def test_slot_ds_add_and_remove_endpoints(client, event_admin, user_model):
    from django.urls import reverse

    from apps.events.models import SlotDS

    client.force_login(event_admin)
    today = date.today()
    event = Event.objects.create(title="E", start_date=today, end_date=today + timedelta(days=7), visible=True)
    squad, grid, selection = _ds_squad_grid_slot(event)
    ds_user = _ds_user(user_model, name="Helper", discord_id="900400")

    add_url = reverse("events:slot_ds_add", args=[event.pk, squad.pk, grid.id, selection.pk, ds_user.pk])
    with patch("apps.events.ds_service.add_discord_role", return_value=True):
        resp = client.post(add_url)
    assert resp.status_code == 200
    assert resp["content-type"].startswith("text/event-stream")
    body = b"".join(resp.streaming_content).decode()
    assert "Helper" in body and f"ds-list-{selection.pk}" in body
    ds = SlotDS.objects.get(selection=selection, user=ds_user)
    assert ds.role_was_assigned is True

    remove_url = reverse("events:slot_ds_remove", args=[event.pk, squad.pk, grid.id, selection.pk, ds_user.pk])
    with patch("apps.events.ds_service.remove_discord_role", return_value=True):
        resp2 = client.post(remove_url)
    assert resp2.status_code == 200
    assert not SlotDS.objects.filter(selection=selection, user=ds_user).exists()


@pytest.mark.django_db
def test_thread_message_includes_ds(user_model):
    from apps.events.views import _build_slot_thread_message

    today = date.today()
    event = Event.objects.create(title="E", start_date=today, end_date=today + timedelta(days=7), visible=True)
    _squad, _grid, selection = _ds_squad_grid_slot(event)
    ds_user = _ds_user(user_model, name="Sporty", discord_id="900500")
    selection.directeurs_sportifs.add(ds_user)
    body, ids = _build_slot_thread_message(selection)
    assert "DS:" in body
    assert "900500" in ids


def test_ds_templates_use_datastar_colon_event_syntax():
    # Guard: Datastar event handlers must be data-on:<event>, not data-on-<event>
    # (the hyphen form is silently ignored, so the panel does nothing).
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent.parent / "templates" / "events"
    for name in ("_slot_ds_list.html", "_slot_ds_results.html", "_slot_selections_container.html"):
        text = (base / name).read_text()
        assert "data-on-" not in text, f"{name} uses hyphen data-on- (should be data-on:)"
    assert "data-on:" in (base / "_slot_selections_container.html").read_text()


# --------------------------------------------------------------------------- #
# Eligibility page: grouping + expiring filter
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_eligibility_group_by_squad(client, superuser, user_model):
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(superuser)
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True)
    squad_a = Squad.objects.create(event=event, name="Squad A")
    squad_b = Squad.objects.create(event=event, name="Squad B")

    multi = user_model.objects.create(username="multi", first_name="Multixyz")
    solo = user_model.objects.create(username="solo", first_name="Soloxyz")
    for u in (multi, solo):
        EventSignup.objects.create(event=event, user=u, status=EventSignup.Status.REGISTERED)
    # multi is a member of both squads; solo is in no squad.
    SquadMember.objects.create(squad=squad_a, user=multi, status=SquadMember.Status.MEMBER)
    SquadMember.objects.create(squad=squad_b, user=multi, status=SquadMember.Status.MEMBER)

    body = client.get(reverse("events:squad_v_report", args=[event.pk]), {"group": "squad"}).content.decode()
    assert "Squad A" in body
    assert "Squad B" in body
    assert "Unassigned" in body
    assert body.count("Multixyz") >= 2  # appears under both squads
    assert "Soloxyz" in body  # appears under Unassigned


@pytest.mark.django_db
def test_eligibility_expiring_filter(client, superuser, user_model, verification_factory):
    from constance import config
    from django.urls import reverse

    client.force_login(superuser)
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True)

    wl_days = config.WEIGHT_LIGHT_DAYS  # default validity for weight_light
    soon = user_model.objects.create(username="soon", first_name="Soonxyz", is_race_ready=True)
    later = user_model.objects.create(username="later", first_name="Laterxyz", is_race_ready=True)
    for u in (soon, later):
        EventSignup.objects.create(event=event, user=u, status=EventSignup.Status.REGISTERED)
    # 'soon' expires in ~3 days; 'later' has the full validity window remaining.
    verification_factory(soon, "weight_light", days_ago=wl_days - 3)
    verification_factory(later, "weight_light", days_ago=0)

    body = client.get(reverse("events:squad_v_report", args=[event.pk]), {"expiring": "7"}).content.decode()
    assert "Soonxyz" in body
    assert "Laterxyz" not in body


@pytest.mark.django_db
def test_eligibility_squads_tab_flags_out_of_bounds(client, superuser, user_model, zp_team_rider_factory):
    from django.urls import reverse

    from apps.events.models import Squad, SquadMember

    client.force_login(superuser)
    today = date.today()
    event = Event.objects.create(title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True)
    # Squad caps Zwift category at B (strongest allowed).
    squad = Squad.objects.create(
        event=event, name="B Squad", max_zwift_category="B", enforce_max_zwift_category=True
    )

    over = user_model.objects.create(username="over", first_name="Overxyz", zwid=90001)
    zp_team_rider_factory(zwid=90001, div=10)  # Cat A -> above the B cap
    ok_rider = user_model.objects.create(username="okr", first_name="Okayxyz", zwid=90002)
    zp_team_rider_factory(zwid=90002, div=20)  # Cat B -> within
    SquadMember.objects.create(squad=squad, user=over, status=SquadMember.Status.MEMBER)
    SquadMember.objects.create(squad=squad, user=ok_rider, status=SquadMember.Status.MEMBER)

    body = client.get(reverse("events:squad_v_report", args=[event.pk])).content.decode()
    assert "Overxyz" in body  # flagged: Cat A above the squad's B maximum
    assert "maximum (B)" in body  # the violation reason is shown (apostrophe is HTML-escaped)
    # The within-limits rider should not be listed as a violation.
    # (Both names exist in the ZP data, but only the violator appears in the Squads section.)
    assert body.count("Okayxyz") == 0
