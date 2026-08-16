"""Tests for the verification media purge views clearing the media_file column."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.team.models import RaceReadyRecord


@pytest.fixture
def purger(user_model):
    """Build a user allowed to run the media purge actions.

    Returns:
        A user with team_member + approve_verification.

    """
    return user_model.objects.create_user(
        username="purger",
        email="purger@example.test",
        permission_overrides={"team_member": True, "approve_verification": True},
    )


def _attach_media(record: RaceReadyRecord) -> None:
    """Attach a small uploaded file to a record.

    Args:
        record: The record to attach evidence to.

    """
    record.media_file = SimpleUploadedFile("evidence.jpg", b"not-a-real-jpeg", content_type="image/jpeg")
    record.save(update_fields=["media_file"])


@pytest.mark.django_db
def test_expired_purge_clears_media_file_column(client, purger, user, verification_factory) -> None:
    """Purging expired media must persist the cleared media_file, not just the url."""
    record = verification_factory(
        user, "weight_light", status=RaceReadyRecord.Status.VERIFIED, days_ago=400, weight=70
    )
    _attach_media(record)
    assert record.is_expired  # precondition: the purge only touches expired records

    client.force_login(purger)
    client.post(reverse("team:delete_expired_media"))

    record.refresh_from_db()
    assert not record.media_file  # the column, not just the in-memory FieldFile
    assert record.url == ""


@pytest.mark.django_db
def test_rejected_purge_clears_media_file_column(client, purger, user, verification_factory) -> None:
    """Purging old rejected media must persist the cleared media_file too."""
    record = verification_factory(user, "height", status=RaceReadyRecord.Status.REJECTED, height=175)
    record.reviewed_date = timezone.now() - timedelta(days=60)
    record.save(update_fields=["reviewed_date"])
    _attach_media(record)

    client.force_login(purger)
    client.post(reverse("team:delete_rejected_media"))

    record.refresh_from_db()
    assert not record.media_file
    assert record.url == ""
