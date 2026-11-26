"""The "only Race Verified riders may answer" setting on availability templates.

A captain reported that a template marked Race Verified only made weekly scheduled sheets
anyone could answer: templates never kept the setting, Save as Template never sent it, and
``availability_schedules.grid_from_template`` -- behind both a hand-applied template and the
schedule -- never set it.
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


@pytest.fixture
def event(db) -> Event:
    """Build a league whose event does not itself require Race Verified.

    Returns:
        The event.

    """
    return Event.objects.create(title="Ladder", start_date=date(2026, 11, 1), end_date=date(2027, 1, 31), visible=True)


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a channel and a role, for the schedule to post to.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Alpha", discord_channel_id=111, team_discord_role=222)


def _template(squad, *, race_verified: bool) -> AvailabilityGridTemplate:
    """Build the squad's evening template.

    Returns:
        The template.

    """
    return AvailabilityGridTemplate.objects.create(
        squad=squad,
        name="Evenings",
        start_time="19:00",
        end_time="21:00",
        grid_timezone="Europe/London",
        slot_duration=60,
        default_length_days=7,
        require_race_verified_availability=race_verified,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("ticked", [True, False])
def test_save_as_template_keeps_the_setting(client, event, squad, user_model, ticked) -> None:
    captain = _make_user(user_model, username="cap", permissions={"team_member": True})
    squad.captains.add(captain)
    client.force_login(captain)
    payload = {
        "name": "Evenings",
        "start_time": "19:00",
        "end_time": "21:00",
        "timezone": "Europe/London",
        "slot_duration": 60,
        "length_days": 7,
        "require_race_verified_availability": ticked,
    }

    resp = client.post(
        reverse("events:availability_template_create", args=[event.pk, squad.pk]),
        json.dumps(payload),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert AvailabilityGridTemplate.objects.get().require_race_verified_availability is ticked


def test_the_builder_sends_the_setting_with_save_as_template() -> None:
    from pathlib import Path

    from django.conf import settings

    builder = (Path(settings.BASE_DIR) / "templates/events/availability_builder.html").read_text()
    save = builder[builder.index("var onSaveTemplateClick") : builder.index("templateButtons.forEach")]

    assert "require_race_verified_availability: requireRaceVerifiedEl.checked" in save


@pytest.mark.django_db
@pytest.mark.parametrize("ticked", [True, False])
def test_a_hand_applied_template_carries_it(client, event, squad, user_model, ticked) -> None:
    template = _template(squad, race_verified=ticked)
    captain = _make_user(user_model, username="cap", permissions={"team_member": True})
    squad.captains.add(captain)
    client.force_login(captain)

    client.post(
        reverse("events:availability_template_apply", args=[event.pk, squad.pk, template.pk]),
        {"start_date": "2026-11-27"},
    )

    grid = AvailabilityGrid.objects.get()
    assert grid.require_race_verified_availability is ticked
    assert grid.require_race_verified_effective is ticked


@pytest.mark.django_db
def test_the_weekly_schedule_posts_sheets_only_race_verified_riders_can_answer(squad) -> None:
    """The report: the schedule's sheets dropped the template's Race Verified requirement."""
    AvailabilitySchedule.objects.create(
        squad=squad,
        template=_template(squad, race_verified=True),
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
    assert grid.require_race_verified_availability


@pytest.mark.django_db
def test_an_event_that_requires_it_still_does_whatever_the_template_says(event, squad) -> None:
    event.require_race_verified_availability = True
    event.save(update_fields=["require_race_verified_availability"])

    grid = grid_from_template(_template(squad, race_verified=False), squad, date(2026, 11, 27))

    assert grid.require_race_verified_effective
