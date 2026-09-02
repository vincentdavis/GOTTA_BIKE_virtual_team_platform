"""Guards on Strava club-activity retention.

This table is the one place in the project where retention is not a nicety. Strava's club
feed omits the athlete ID, so we hold ride data for named people with no way to find one
person's rows -- an erasure request against this table could not be honoured. The window is
the only control, which makes "the sweep silently matches nothing" the failure to defend
against.
"""

from datetime import timedelta

import pytest
from constance.test import override_config
from django.utils import timezone

from apps.club_strava.models import ClubActivity
from apps.club_strava.tasks import purge_strava_activities
from gotta_bike_platform.retention import RetentionPolicy, policy_for


@pytest.fixture
def activity_factory(db):
    def _make(strava_id=1, *, created_days_ago=0, activity_date=None):
        row = ClubActivity.objects.create(
            strava_id=strava_id,
            athlete_first_name="Ada",
            athlete_last_name="R",
            name="Morning Ride",
            sport_type="VirtualRide",
            distance=30000,
            moving_time=3600,
            elapsed_time=3700,
            activity_date=activity_date,
        )
        # auto_now_add cannot be set on create.
        ClubActivity.objects.filter(pk=row.pk).update(
            date_created=timezone.now() - timedelta(days=created_days_ago)
        )
        return row

    return _make


@pytest.mark.django_db
@override_config(STRAVA_ACTIVITY_MAX_DAYS=120)
def test_activities_past_the_window_are_deleted(activity_factory):
    activity_factory(1, created_days_ago=200)
    activity_factory(2, created_days_ago=10)

    result = purge_strava_activities.func()

    assert result["deleted"] == 1
    assert set(ClubActivity.objects.values_list("strava_id", flat=True)) == {2}


@pytest.mark.django_db
@override_config(STRAVA_ACTIVITY_MAX_DAYS=120)
def test_the_sweep_anchors_on_ingest_not_the_ride_date(activity_factory):
    """activity_date is null on every row Strava gives us, so anchoring there matches nothing.

    This test would pass trivially if the anchor were activity_date and the row had one, so it
    deliberately sets a recent activity_date on an old row: the row must still go.
    """
    activity_factory(1, created_days_ago=200, activity_date=timezone.now())

    result = purge_strava_activities.func()

    assert result["deleted"] == 1, "an old row must age out even when it carries a recent ride date"


@pytest.mark.django_db
@override_config(STRAVA_ACTIVITY_MAX_DAYS=120)
def test_rows_with_no_ride_date_are_still_reachable(activity_factory):
    """The real-world case: Strava's club feed omits the date, so every row looks like this."""
    activity_factory(1, created_days_ago=200, activity_date=None)

    assert purge_strava_activities.func()["deleted"] == 1


@pytest.mark.django_db
@override_config(STRAVA_ACTIVITY_MAX_DAYS=0)
def test_zero_disables_the_sweep(activity_factory):
    activity_factory(1, created_days_ago=9999)

    result = purge_strava_activities.func()

    assert result["deleted"] == 0
    assert ClubActivity.objects.count() == 1


@pytest.mark.django_db
def test_the_configured_default_is_120_days():
    from constance import config

    assert config.STRAVA_ACTIVITY_MAX_DAYS == 120


def test_the_model_declares_its_retention():
    policy = policy_for(ClubActivity)

    assert policy is not None
    assert policy.kind == RetentionPolicy.KIND_DELETE
    assert policy.anchor == "date_created"
    assert policy.setting == "STRAVA_ACTIVITY_MAX_DAYS"
    assert policy.task == "purge_strava_activities"


@pytest.mark.django_db
def test_the_sweep_is_scheduled_every_48_hours():
    from gotta_bike_platform.task_registry import get_scheduled_tasks

    job = next(j for j in get_scheduled_tasks() if j["id"] == "purge_strava_activities")

    assert job["minutes"] == 48 * 60
