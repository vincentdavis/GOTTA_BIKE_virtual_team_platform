"""Expiring-verification DMs survive a missed run.

Riders reported not receiving the warning that their Race Verified status was about to expire.
The cause was structural rather than a broken send: the task matched ``days_remaining`` for
exact equality against EXPIRE_WARNING_DAYS, so each threshold was live for exactly one
calendar day, while the job runs once every 24 hours on an interval anchored at process boot.
Every deploy shifts that slot, and some calendar days therefore get no run at all -- a rider
sitting on 15 days that day lost the 15-day warning permanently, and nothing recorded that a
warning had been owed.

The task now warns on the highest threshold a record has CROSSED but not yet been served,
recorded in ``last_warned_threshold``. Any later run is a catch-up rather than a no-op, and
each threshold is still served at most once.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from constance.test import override_config
from django.utils import timezone

from apps.team.models import RaceReadyRecord
from apps.team.tasks import _threshold_due, warn_expiring_verifications

THRESHOLDS = [15, 7, 3, 1, 0]


@pytest.fixture
def rider(user_model, db):
    """Build a rider reachable by DM.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider", email="rider@example.test", discord_id="777001",
        first_name="Ada", last_name="Test",
    )


def _record(rider, days_left: int) -> RaceReadyRecord:
    """Create a verified weight record expiring in ``days_left`` days.

    WEIGHT_FULL_DAYS is 120, so the record_date is back-dated to place expiry where we want it.

    Args:
        rider: The record's owner.
        days_left: Days until expiry.

    Returns:
        The record.

    """
    return RaceReadyRecord.objects.create(
        user=rider,
        verify_type="weight_full",
        status=RaceReadyRecord.Status.VERIFIED,
        record_date=timezone.now().date() - timedelta(days=120 - days_left),
    )


# ---------------------------------------------------------------- the helper


def test_a_crossed_but_unserved_threshold_is_due():
    """The whole point: at 14 days the 15-day warning is late, not forfeit."""
    assert _threshold_due(14, None, THRESHOLDS) == 15


def test_an_exact_threshold_day_is_due():
    assert _threshold_due(15, None, THRESHOLDS) == 15


def test_nothing_is_due_before_the_first_threshold():
    assert _threshold_due(16, None, THRESHOLDS) is None


def test_a_served_threshold_is_not_repeated():
    """Served largest-first and once each, so extra runs the same week send nothing."""
    assert _threshold_due(14, 15, THRESHOLDS) is None
    assert _threshold_due(9, 15, THRESHOLDS) is None


def test_the_next_threshold_down_becomes_due():
    assert _threshold_due(7, 15, THRESHOLDS) == 7
    assert _threshold_due(2, 7, THRESHOLDS) == 3


def test_a_lapsed_record_is_due_the_most_urgent_threshold():
    """Negative days still cross every threshold; the rider hears once, not five times."""
    assert _threshold_due(-4, None, THRESHOLDS) == 0
    assert _threshold_due(-4, 0, THRESHOLDS) is None


# ---------------------------------------------------------------- the task


def _run(sent):
    """Run the task with Discord patched, recording each DM.

    Args:
        sent: List that receives (discord_id, message) per send.

    Returns:
        The task's summary dict.

    """
    def _fake(discord_id, message):
        sent.append((discord_id, message))
        return True

    with patch("apps.team.tasks.send_discord_dm", side_effect=_fake), patch("apps.team.tasks.time.sleep"):
        return warn_expiring_verifications.func()


@pytest.mark.django_db
@override_config(EXPIRE_WARNING_DAYS="[15, 7, 3, 1, 0]")
def test_a_run_that_misses_the_threshold_day_still_warns(rider):
    """The reported bug. Under exact matching this rider got nothing, ever."""
    _record(rider, days_left=14)  # 15-day threshold already passed; no run landed on it
    sent = []

    result = _run(sent)

    assert len(sent) == 1, "the late 15-day warning should still go out"
    assert result["warnings_sent"] == 1


@pytest.mark.django_db
@override_config(EXPIRE_WARNING_DAYS="[15, 7, 3, 1, 0]")
def test_a_rider_unreachable_for_a_week_still_gets_the_urgent_warning(rider):
    """Missing several thresholds must not compound into silence."""
    _record(rider, days_left=2)  # 15 and 7 both missed
    sent = []

    _run(sent)

    assert len(sent) == 1
    assert RaceReadyRecord.objects.get(user=rider).last_warned_threshold == 3


@pytest.mark.django_db
@override_config(EXPIRE_WARNING_DAYS="[15, 7, 3, 1, 0]")
def test_each_threshold_is_served_at_most_once(rider):
    """Catch-up must not become a daily nag: a second run the same week sends nothing."""
    record = _record(rider, days_left=14)
    first = []
    _run(first)

    # Clear the one-per-day rate limit so only the threshold bookkeeping can stop a resend.
    record.refresh_from_db()
    record.last_warned_at = timezone.now().date() - timedelta(days=1)
    record.save(update_fields=["last_warned_at"])

    second = []
    _run(second)

    assert len(first) == 1
    assert second == [], "the 15-day warning must not be sent twice"


@pytest.mark.django_db
@override_config(EXPIRE_WARNING_DAYS="[15, 7, 3, 1, 0]")
def test_a_failed_send_leaves_the_warning_owed(rider):
    """A transient Discord failure must not burn the threshold.

    Previously the exact-day match meant tomorrow's run no longer matched, so a failure on the
    threshold day silently cost the rider that warning.
    """
    _record(rider, days_left=15)

    with patch("apps.team.tasks.send_discord_dm", return_value=False), patch("apps.team.tasks.time.sleep"):
        warn_expiring_verifications.func()

    record = RaceReadyRecord.objects.get(user=rider)
    assert record.last_warned_at is None
    assert record.last_warned_threshold is None
    assert _threshold_due(14, record.last_warned_threshold, THRESHOLDS) == 15, "still owed tomorrow"


@pytest.mark.django_db
@override_config(EXPIRE_WARNING_DAYS="[15, 7, 3, 1, 0]")
def test_one_riders_exception_does_not_abandon_the_rest(user_model):
    """The loop had no guard, so an unhandled send error skipped every rider after it.

    The iteration order is stable, so the same riders were silently starved every run.
    """
    first = user_model.objects.create_user(
        username="a", email="a@example.test", discord_id="888001", first_name="A", last_name="One",
    )
    second = user_model.objects.create_user(
        username="b", email="b@example.test", discord_id="888002", first_name="B", last_name="Two",
    )
    _record(first, days_left=15)
    _record(second, days_left=15)
    reached = []

    def _explode(discord_id, message):
        reached.append(discord_id)
        if discord_id == "888001":
            raise RuntimeError("connection reset")
        return True

    with patch("apps.team.tasks.send_discord_dm", side_effect=_explode), patch("apps.team.tasks.time.sleep"):
        result = warn_expiring_verifications.func()

    assert set(reached) == {"888001", "888002"}, "the second rider must still be attempted"
    assert result["warnings_sent"] == 1
    assert len(result["errors"]) == 1
