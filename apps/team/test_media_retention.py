"""A retention cap on verification media, independent of how long a verification is valid.

Height verification never expires -- an adult's height does not change -- and because the
media purge keyed off expiry, height photographs were kept forever. Validity and retention
are separate questions; this is the second one.

These tests are deliberately fussy about boundaries: the sweep deletes files that cannot be
recovered, and the two ways to get it wrong are deleting evidence someone still needs and
quietly deleting nothing at all.
"""

from datetime import timedelta

import pytest
from constance.test import override_config
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.team.models import RaceReadyRecord
from apps.team.services import purge_aged_verification_media


def _record(user, *, status, age_days, verify_type="height"):
    """Create a verification record holding media, aged by backdating date_created.

    Args:
        user: The record's owner.
        status: A RaceReadyRecord.Status value.
        age_days: How long ago the record was created.
        verify_type: The verification type.

    Returns:
        The saved record.

    """
    record = RaceReadyRecord.objects.create(
        user=user,
        verify_type=verify_type,
        status=status,
        record_date=timezone.now().date(),
        media_file=SimpleUploadedFile("proof.jpg", b"not-a-real-image", content_type="image/jpeg"),
    )
    # date_created has a default rather than auto_now_add, so it can be set after the fact.
    RaceReadyRecord.objects.filter(pk=record.pk).update(
        date_created=timezone.now() - timedelta(days=age_days)
    )
    record.refresh_from_db()
    return record


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=365)
def test_height_media_is_purged_even_though_the_verification_never_expires(team_member):
    """The case this exists for."""
    record = _record(team_member, status=RaceReadyRecord.Status.VERIFIED, age_days=400)

    result = purge_aged_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 1
    assert not record.media_file


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=365)
def test_the_record_itself_survives(team_member):
    """Only the evidence goes; the verification stays valid."""
    record = _record(team_member, status=RaceReadyRecord.Status.VERIFIED, age_days=400)

    purge_aged_verification_media()

    record.refresh_from_db()
    assert record.status == RaceReadyRecord.Status.VERIFIED


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=365)
def test_media_inside_the_window_is_left_alone(team_member):
    record = _record(team_member, status=RaceReadyRecord.Status.VERIFIED, age_days=364)

    result = purge_aged_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 0
    assert record.media_file


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=365)
def test_pending_evidence_is_never_swept(team_member):
    """A reviewer has not looked at it yet; deleting it destroys the thing under review."""
    record = _record(team_member, status=RaceReadyRecord.Status.PENDING, age_days=400)

    purge_aged_verification_media()

    record.refresh_from_db()
    assert record.media_file, "pending evidence was deleted before review"


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=0)
def test_zero_keeps_media_indefinitely(team_member):
    """Same convention as the other *_DAYS settings: 0 means no limit."""
    record = _record(team_member, status=RaceReadyRecord.Status.VERIFIED, age_days=5000)

    result = purge_aged_verification_media()

    record.refresh_from_db()
    assert result["purged"] == 0
    assert record.media_file


@pytest.mark.django_db
@override_config(VERIFICATION_MEDIA_MAX_DAYS=365)
def test_a_backdated_record_is_not_purged_on_upload(team_member):
    """Measured from when we took the file, not from the date the evidence depicts.

    A rider may enter a record_date well in the past; measuring retention from that would
    delete the photo the day it arrived.
    """
    record = _record(team_member, status=RaceReadyRecord.Status.VERIFIED, age_days=0)
    record.record_date = timezone.now().date() - timedelta(days=1000)
    record.save(update_fields=["record_date"])

    purge_aged_verification_media()

    record.refresh_from_db()
    assert record.media_file
