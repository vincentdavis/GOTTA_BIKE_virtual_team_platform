"""Creating a scheduled race and its Discord thread in one POST.

This crashed in production: "Save & Create Thread" on a NEW cell raised
``TypeError: combine() argument 1 must be datetime.date, not str``. ``slot_date`` arrives
from the form as a string and was handed straight to ``update_or_create``; Django converts
it on the way into the database but leaves the attribute alone, so the CREATE branch
returned an instance still holding a string while the UPDATE branch, which re-reads the row,
returned a real date.

Everything here therefore goes through the create branch on purpose. A test that saves the
slot first and then posts exercises the update branch, passes with the bug fully present, and
proves nothing -- which is exactly how this survived having tests around it.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.events.models import AvailabilityGrid, AvailabilitySlotSelection, Event, Squad


@pytest.fixture
def thread_ready(db, team_member):
    """Build a squad whose captain can create a thread: channel set, grid published.

    Returns:
        An ``(event, squad, grid)`` tuple with ``team_member`` as the squad captain.

    """
    today = timezone.now().date()
    event = Event.objects.create(
        title="Series",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
    )
    squad = Squad.objects.create(event=event, name="Alpha", discord_channel_id=4242)
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


def _create_url(event, squad, grid):
    return reverse("events:slot_selection_create", args=[event.pk, squad.pk, grid.pk])


def _payload(team_member, *, slot_date="2026-09-20", create_thread="1"):
    """Build the POST body the "Save & Create Thread" button sends.

    Returns:
        The form fields, with every prerequisite _create_slot_thread checks.

    """
    return {
        "name": "Race 1",
        "slot_date": slot_date,
        "slot_time": "18:30",
        # Confirmed with a rider picked: the two guards that would otherwise return early
        # and never reach the code that reads slot_date.
        "status": AvailabilitySlotSelection.Status.CONFIRMED,
        "selected_users": [str(team_member.pk)],
        "create_thread": create_thread,
    }


@pytest.mark.django_db
@override_config(GUILD_ID=791589155654205450)
def test_creating_a_new_slot_and_its_thread_together_does_not_crash(auth_client, thread_ready, team_member):
    """The production 500, end to end."""
    event, squad, grid = thread_ready

    with (
        patch("apps.events.views.create_discord_thread", return_value=("9001", None)) as thread,
        patch("apps.events.views.send_discord_channel_message", return_value=True),
    ):
        response = auth_client.post(_create_url(event, squad, grid), _payload(team_member))

    assert response.status_code == 200
    assert thread.called, "the thread was never attempted, so this never reached the crash"
    assert AvailabilitySlotSelection.objects.get(grid=grid).thread_link.endswith("/9001")


@pytest.mark.django_db
@override_config(GUILD_ID=791589155654205450)
def test_the_thread_is_named_for_the_slots_date(auth_client, thread_ready, team_member):
    """The value that crashed is the one the name is built from, so pin what it produces."""
    event, squad, grid = thread_ready

    with (
        patch("apps.events.views.create_discord_thread", return_value=("9001", None)) as thread,
        patch("apps.events.views.send_discord_channel_message", return_value=True),
    ):
        auth_client.post(_create_url(event, squad, grid), _payload(team_member, slot_date="2026-09-20"))

    _channel, thread_name = thread.call_args.args
    assert thread_name == "Race 1 Sep 20"


@pytest.mark.django_db
@override_config(GUILD_ID=791589155654205450)
def test_the_selection_passed_downstream_carries_a_real_date(auth_client, thread_ready, team_member):
    """The root cause rather than the symptom.

    Written first as ``objects.get(...)`` after the POST, which passed with the bug fully
    present: re-reading the row always gave a date, because the column was never wrong. What
    was wrong is the object ``update_or_create`` RETURNS on its create branch, and that is
    what every caller after the save actually holds. So this inspects the instance the view
    hands on, not the row.
    """
    event, squad, grid = thread_ready
    seen = {}

    def _spy(selection, *_args, **_kwargs):
        # None is what _create_slot_thread returns on success, so the view carries on.
        seen["type"] = type(selection.slot_date)
        seen["value"] = selection.slot_date

    with patch("apps.events.views._create_slot_thread", side_effect=_spy):
        auth_client.post(_create_url(event, squad, grid), _payload(team_member))

    assert seen["type"] is date, f"downstream received {seen['type'].__name__}, not a date"
    assert seen["value"] == date(2026, 9, 20)


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["2026-02-31", "tomorrow", "20/09/2026", "2026-9-20x"])
def test_a_date_that_is_not_a_date_is_refused_here(auth_client, thread_ready, team_member, bad):
    """The emptiness check above it calls "2026-02-31" present, and it is not a date.

    Without parsing, these reach the database driver and 500 there instead of being refused.
    """
    event, squad, grid = thread_ready

    response = auth_client.post(_create_url(event, squad, grid), _payload(team_member, slot_date=bad))

    assert response.status_code == 400
    assert not AvailabilitySlotSelection.objects.filter(grid=grid).exists()


@pytest.mark.django_db
@override_config(GUILD_ID=791589155654205450)
def test_saving_over_an_existing_slot_still_works(auth_client, thread_ready, team_member):
    """The update branch was never broken; the fix must not break it.

    It takes the other path through update_or_create, so it is the half that would notice a
    parsed date failing as a lookup value.
    """
    event, squad, grid = thread_ready
    AvailabilitySlotSelection.objects.create(
        grid=grid, name="Old name", slot_date=date(2026, 9, 20), slot_time="18:30"
    )

    with (
        patch("apps.events.views.create_discord_thread", return_value=("9001", None)),
        patch("apps.events.views.send_discord_channel_message", return_value=True),
    ):
        response = auth_client.post(_create_url(event, squad, grid), _payload(team_member))

    assert response.status_code == 200
    assert AvailabilitySlotSelection.objects.filter(grid=grid).count() == 1, "it must update, not duplicate"
    assert AvailabilitySlotSelection.objects.get(grid=grid).name == "Race 1"
