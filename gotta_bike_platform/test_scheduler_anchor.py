"""Scheduled jobs keep their slot across restarts.

An IntervalTrigger with no start_date counts from the moment the scheduler process booted, so
a daily job fired at boot+24h. Every deploy re-anchored it, walking the slot forward through
the day and occasionally skipping a calendar date entirely -- which is how expiring
verification warnings were being missed, since each threshold was live for one day only.

Anchoring to midnight UTC makes the schedule a property of the clock rather than of the last
deploy.
"""

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from gotta_bike_platform.management.commands.scheduler import _anchor_for

DAILY = 24 * 60


def _next_fires(boot: datetime, minutes: float, count: int = 4) -> list[datetime]:
    """Fire times a job would take, given a scheduler booting at ``boot``.

    Args:
        boot: When the scheduler process started.
        minutes: The job's interval.
        count: How many fire times to collect.

    Returns:
        The fire times.

    """
    trigger = IntervalTrigger(minutes=minutes, start_date=_anchor_for(minutes))
    fires: list[datetime] = []
    previous, now = None, boot
    while len(fires) < count:
        nxt = trigger.get_next_fire_time(previous, now)
        if nxt is None:
            break
        fires.append(nxt)
        previous, now = nxt, nxt + timedelta(seconds=1)
    return fires


def test_the_anchor_is_midnight_utc():
    anchor = _anchor_for(DAILY)

    assert (anchor.hour, anchor.minute, anchor.second, anchor.microsecond) == (0, 0, 0, 0)
    assert anchor.tzinfo == UTC


def test_a_daily_job_anchors_far_enough_back_to_fire_within_one_interval():
    """Anchored to today's midnight, a daily job booted at 09:00 would wait until tomorrow."""
    assert _anchor_for(DAILY) < datetime.now(UTC) - timedelta(hours=23)


def test_a_sub_daily_job_anchors_to_today():
    """An hourly job needs no backdating; today's midnight keeps it on the hour."""
    anchor = _anchor_for(60)

    assert anchor.date() == datetime.now(UTC).date()


@pytest.mark.parametrize("boot_hour", [0, 9, 15, 23])
def test_the_daily_slot_is_the_same_whatever_time_the_process_booted(boot_hour):
    """The regression: a deploy at 15:00 used to move the job to 15:00 forever after."""
    boot = datetime.now(UTC).replace(hour=boot_hour, minute=17, second=0, microsecond=0)

    fires = _next_fires(boot, DAILY)

    assert {f.time() for f in fires} == {datetime.min.time()}, "every fire should be at midnight UTC"


def test_two_different_boot_times_produce_identical_schedules():
    """Two deploys on the same day must not end up on different slots."""
    today = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    early = _next_fires(today.replace(hour=8), DAILY)
    late = _next_fires(today.replace(hour=21), DAILY)

    assert early == late


def test_intervals_stay_evenly_spaced():
    """Anchoring must not disturb the cadence itself."""
    fires = _next_fires(datetime.now(UTC), DAILY, count=4)

    gaps = {(b - a) for a, b in pairwise(fires)}
    assert gaps == {timedelta(days=1)}


@pytest.mark.django_db
def test_the_command_actually_passes_the_anchor_to_add_job():
    """The helper is only useful if add_job uses it.

    Every other test here exercises ``_anchor_for`` directly, so they all keep passing if the
    trigger stops being anchored -- which is exactly the regression being guarded against.
    """
    from unittest.mock import Mock, patch

    from django.core.management import call_command

    fake = Mock()
    with patch("gotta_bike_platform.management.commands.scheduler.BlockingScheduler", return_value=fake):
        call_command("scheduler")

    assert fake.add_job.called, "no jobs registered"
    for call in fake.add_job.call_args_list:
        trigger = call.kwargs["trigger"]
        # APScheduler DEFAULTS start_date to "now" when it is omitted, so `is not None`
        # would pass for an unanchored trigger. Compare against the anchor itself.
        expected = _anchor_for(trigger.interval.total_seconds() / 60)
        assert trigger.start_date == expected, (
            f"{call.kwargs['id']} is anchored at {trigger.start_date}, not {expected} -- "
            f"an unanchored trigger silently starts from process boot"
        )
        assert call.kwargs["misfire_grace_time"] > 1, (
            f"{call.kwargs['id']} keeps APScheduler's 1s grace, which drops a missed run"
        )
        assert call.kwargs["coalesce"] is True
