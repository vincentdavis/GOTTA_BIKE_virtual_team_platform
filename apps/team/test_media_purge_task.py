"""Tests for the daily verification-media purge task and its shared service.

Purging used to be two manual admin buttons, so a rider's scale photo sat in storage until
someone remembered to click. These tests cover the scheduled sweep that replaced that as
the routine path, and the boundaries of what it is allowed to touch.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from constance import config
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.team.models import RaceReadyRecord
from apps.team.services import purge_expired_verification_media, purge_rejected_verification_media
from apps.team.tasks import purge_expired_media
from gotta_bike_platform.task_registry import TASK_REGISTRY, resolve_interval_minutes


def _with_media(record: RaceReadyRecord) -> RaceReadyRecord:
    """Attach a small uploaded file to a record.

    Returns:
        The same record, with evidence attached.

    """
    record.media_file = SimpleUploadedFile("evidence.jpg", b"not-a-real-jpeg", content_type="image/jpeg")
    record.save(update_fields=["media_file"])
    return record


@pytest.mark.django_db
def test_expired_record_loses_both_its_file_and_its_url(user, verification_factory) -> None:
    """An expired verification is no longer a live claim, so its evidence goes."""
    record = _with_media(verification_factory(user, "weight_light", days_ago=config.WEIGHT_LIGHT_DAYS + 5))

    result = purge_expired_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 1
    assert not record.media_file
    assert record.url == ""


@pytest.mark.django_db
def test_a_still_valid_verification_keeps_its_evidence(user, verification_factory) -> None:
    """Media on a live claim is the proof behind it and must not be swept up."""
    record = _with_media(verification_factory(user, "weight_light", days_ago=1))

    result = purge_expired_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 0
    assert record.media_file


@pytest.mark.django_db
def test_a_pending_record_is_never_touched(user, verification_factory) -> None:
    """Nobody has reviewed it yet -- deleting the evidence would strand the review."""
    record = _with_media(
        verification_factory(user, "weight_light", status=RaceReadyRecord.Status.PENDING, days_ago=400)
    )

    result = purge_expired_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 0
    assert record.media_file


@pytest.mark.django_db
def test_a_never_expiring_type_is_left_alone(user, verification_factory) -> None:
    """A *_DAYS setting of 0 means "never expires", so the sweep can never reach it."""
    config.HEIGHT_VERIFICATION_DAYS = 0
    record = _with_media(verification_factory(user, "height", days_ago=3650))

    result = purge_expired_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 0
    assert record.media_file


@pytest.mark.django_db
def test_one_unreadable_file_does_not_abort_the_sweep(user, verification_factory) -> None:
    """A single bad blob must not leave the rest of the backlog un-purged."""
    days = config.WEIGHT_LIGHT_DAYS + 5
    doomed = _with_media(verification_factory(user, "weight_light", days_ago=days))
    healthy = _with_media(verification_factory(user, "weight_full", days_ago=config.WEIGHT_FULL_DAYS + 5))

    real_delete = RaceReadyRecord.delete_media_file

    def _explode_once(self):
        if self.pk == doomed.pk:
            raise OSError("storage unavailable")
        return real_delete(self)

    with patch.object(RaceReadyRecord, "delete_media_file", _explode_once):
        result = purge_expired_verification_media()

    healthy.refresh_from_db()
    assert result["failed"] == 1
    assert result["purged"] == 1
    assert not healthy.media_file


@pytest.mark.django_db
def test_rejected_media_survives_the_grace_period_then_goes(user, verification_factory) -> None:
    """A rider gets a window to query a rejection before the evidence is thrown away."""
    recent = verification_factory(user, "weight_light", status=RaceReadyRecord.Status.REJECTED)
    recent.reviewed_date = timezone.now() - timedelta(days=5)
    recent.save(update_fields=["reviewed_date"])
    _with_media(recent)

    old = verification_factory(user, "weight_full", status=RaceReadyRecord.Status.REJECTED)
    old.reviewed_date = timezone.now() - timedelta(days=45)
    old.save(update_fields=["reviewed_date"])
    _with_media(old)

    result = purge_rejected_verification_media(older_than_days=30)

    recent.refresh_from_db()
    old.refresh_from_db()
    assert result["purged"] == 1
    assert recent.media_file
    assert not old.media_file


@pytest.mark.django_db
def test_the_task_runs_the_same_sweep(user, verification_factory) -> None:
    """The scheduled entry point must do what the service does, not a copy of it."""
    record = _with_media(verification_factory(user, "weight_light", days_ago=config.WEIGHT_LIGHT_DAYS + 5))

    result = purge_expired_media.func()

    record.refresh_from_db()
    assert result["purged"] == 1
    assert not record.media_file


@pytest.mark.django_db
def test_the_task_is_registered_to_run_daily() -> None:
    """A purge nobody scheduled is the problem this replaced."""
    entry = TASK_REGISTRY["purge_expired_media"]

    assert entry["scheduled"] is True
    assert entry["hours_setting"] == "SCHEDULER_PURGE_EXPIRED_MEDIA_HOURS"
    assert resolve_interval_minutes(entry) == 24 * 60
