"""Blocked times on availability templates, and on every sheet made from one.

A captain reported that blocks set in the builder were lost once saved as a template, so the
weekly schedule's sheets let riders pick times the squad had ruled out. A template now keeps
its blocks by weekday and local time (``AvailabilityGridTemplate.blocked_slots``), and
``availability_schedules.grid_from_template`` -- used by both a hand-applied template and the
schedule -- places them on the new sheet's own cells.

London is on GMT in November, so local and UTC agree there; the daylight-saving test uses the
week the clocks go forward (Sunday 29 March 2026).
"""

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.discord_service import ChannelPostResult
from apps.events.availability_schedules import grid_from_template, run_schedules
from apps.events.models import AvailabilityGrid, AvailabilityGridTemplate, AvailabilitySchedule, Event, Squad
from apps.events.test_availability_schedules import FRIDAY, at
from conftest import _make_user

SAT, WED, SUN, MON = 5, 2, 6, 0


@pytest.fixture
def event(db) -> Event:
    """Build a league that runs into the new year.

    Returns:
        The event.

    """
    return Event.objects.create(title="Ladder", start_date=date(2026, 3, 1), end_date=date(2027, 1, 31), visible=True)


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a channel and a role, for the schedule to post to.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Alpha", discord_channel_id=111, team_discord_role=222)


@pytest.fixture
def captain(squad, user_model):
    """Build the squad's captain.

    Returns:
        The user.

    """
    user = _make_user(user_model, username="cap", permissions={"team_member": True})
    squad.captains.add(user)
    return user


def _template(squad, blocked, **extra) -> AvailabilityGridTemplate:
    """Build the squad's evening template, 19:00-21:00 London in hour slots, a week long.

    Returns:
        The template.

    """
    fields = {
        "name": "Evenings",
        "start_time": "19:00",
        "end_time": "21:00",
        "grid_timezone": "Europe/London",
        "slot_duration": 60,
        "default_length_days": 7,
        "blocked_slots": [{"weekday": weekday, "time": at_} for weekday, at_ in blocked],
        **extra,
    }
    return AvailabilityGridTemplate.objects.create(squad=squad, **fields)


def _cells(grid: AvailabilityGrid) -> set[tuple[str, str]]:
    return {(cell["date"], cell["time"]) for cell in grid.blocked_cells}


def _save_payload(**extra) -> dict:
    """Build the builder's Save as Template request body.

    Returns:
        The payload.

    """
    return {
        "name": "Evenings",
        "shared": False,
        "start_time": "19:00",
        "end_time": "21:00",
        "timezone": "Europe/London",
        "slot_duration": 60,
        "length_days": 7,
        **extra,
    }


# -- Saving ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_as_template_keeps_the_blocked_times(client, event, squad, captain) -> None:
    client.force_login(captain)
    slots = [
        {"weekday": SAT, "time": "20:00"},
        {"weekday": WED, "time": "20:00"},
        {"weekday": SAT, "time": "19:00"},
        {"weekday": SAT, "time": "20:00"},  # the same time on a second Saturday of a long grid
    ]

    resp = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        json.dumps(_save_payload(blocked_slots=slots)),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert AvailabilityGridTemplate.objects.get().blocked_slots == [
        {"weekday": WED, "time": "20:00"},
        {"weekday": SAT, "time": "19:00"},
        {"weekday": SAT, "time": "20:00"},
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "slots",
    [
        "Saturday",
        [{"weekday": 7, "time": "19:00"}],
        [{"weekday": True, "time": "19:00"}],
        [{"weekday": "5", "time": "19:00"}],
        [{"weekday": SAT, "time": "25:00"}],
        [{"weekday": SAT, "time": "7pm"}],
        ["Saturday 19:00"],
    ],
)
def test_malformed_blocked_times_are_refused(client, event, squad, captain, slots) -> None:
    client.force_login(captain)

    resp = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        json.dumps(_save_payload(blocked_slots=slots)),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert not AvailabilityGridTemplate.objects.exists()


@pytest.mark.django_db
def test_a_template_saved_without_blocks_still_saves(client, event, squad, captain) -> None:
    client.force_login(captain)

    resp = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        json.dumps(_save_payload()),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert AvailabilityGridTemplate.objects.get().blocked_slots == []


def test_the_builder_sends_its_blocks_with_save_as_template() -> None:
    from pathlib import Path

    from django.conf import settings

    builder = (Path(settings.BASE_DIR) / "templates/events/availability_builder.html").read_text()
    save = builder[builder.index("var onSaveTemplateClick") : builder.index("templateButtons.forEach")]

    assert "payload.blocked_slots" in save
    assert "=== 'blocked'" in save


# -- Making sheets -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_hand_applied_template_blocks_the_same_days(client, event, squad, captain) -> None:
    template = _template(squad, [(SAT, "19:00"), (SAT, "20:00"), (WED, "20:00")])
    client.force_login(captain)

    client.post(
        reverse("events:availability_template_apply", args=[event.pk, squad.pk, template.pk]),
        {"start_date": "2026-11-27"},  # a Friday
    )

    assert _cells(AvailabilityGrid.objects.get()) == {
        ("2026-11-28", "19:00"),
        ("2026-11-28", "20:00"),
        ("2026-12-02", "20:00"),
    }


@pytest.mark.django_db
def test_the_weekly_schedule_posts_sheets_with_the_blocks(squad) -> None:
    """The report: the schedule's sheets let riders pick times the template had blocked."""
    template = _template(squad, [(SAT, "19:00"), (SAT, "20:00"), (WED, "20:00")])
    AvailabilitySchedule.objects.create(
        squad=squad,
        template=template,
        weekday=4,
        post_time="18:00",
        schedule_timezone="Europe/London",
        starts_after_days=7,
        reminder_days=[],
        close_after_days=6,
        not_before=at(FRIDAY - timedelta(days=1), 9),
        base_url="https://app.example.test/",
    )

    with patch("apps.events.views.post_discord_channel_message", return_value=ChannelPostResult(True, 200)):
        run_schedules(at(FRIDAY, 18, 5))

    grid = AvailabilityGrid.objects.get()
    assert grid.status == AvailabilityGrid.Status.PUBLISHED
    assert _cells(grid) == {("2026-11-28", "19:00"), ("2026-11-28", "20:00"), ("2026-12-02", "20:00")}


@pytest.mark.django_db
def test_a_block_outside_the_sheets_hours_is_dropped(squad) -> None:
    grid = grid_from_template(_template(squad, [(SAT, "08:00")]), squad, date(2026, 11, 27))

    assert grid.blocked_cells == []


@pytest.mark.django_db
def test_a_re_timed_sheet_keeps_only_the_blocks_it_has_slots_for(squad) -> None:
    """Applied from 20:00, the sheet's slots are 20:00 and 21:00: the 19:00 block has no cell."""
    template = _template(squad, [(SAT, "19:00"), (SAT, "20:00")])

    grid = grid_from_template(template, squad, date(2026, 11, 27), local_start_time="20:00")

    assert _cells(grid) == {("2026-11-28", "20:00")}


@pytest.mark.django_db
def test_blocks_repeat_every_week_of_a_longer_sheet(squad) -> None:
    template = _template(squad, [(SAT, "19:00")], default_length_days=14)

    grid = grid_from_template(template, squad, date(2026, 11, 27))

    assert _cells(grid) == {("2026-11-28", "19:00"), ("2026-12-05", "19:00")}


@pytest.mark.django_db
def test_blocks_follow_local_time_across_the_clocks_going_forward(squad) -> None:
    """Each day is converted on its own: Sunday's 20:00 is 19:00 UTC once the clocks go forward.

    Monday is on GMT, so its 19:00 block is 19:00 UTC. A sheet keeps one UTC start time all
    week, so after the change Sunday's first slot reads 20:00: the Sunday 20:00 block lands on
    it, and the Sunday 19:00 block has no cell that day and is dropped.
    """
    template = _template(squad, [(MON, "19:00"), (SUN, "19:00"), (SUN, "20:00")])

    grid = grid_from_template(template, squad, date(2026, 3, 23))  # Monday; clocks change Sunday 29th

    assert _cells(grid) == {("2026-03-23", "19:00"), ("2026-03-29", "19:00")}


@pytest.mark.django_db
def test_a_template_in_another_zone_blocks_its_own_local_times(squad) -> None:
    """New York 19:00 is midnight UTC: the block lands on the next UTC date, the sheet's real cell."""
    template = _template(squad, [(SAT, "19:00")], grid_timezone="America/New_York")

    grid = grid_from_template(template, squad, date(2026, 11, 27))

    assert _cells(grid) == {("2026-11-29", "00:00")}


@pytest.mark.django_db
def test_a_single_slot_template_blocks_nothing(squad) -> None:
    template = _template(squad, [(SAT, "19:00")], single_slot=True, default_length_days=1)

    assert grid_from_template(template, squad, date(2026, 11, 28)).blocked_cells == []
