"""Deleting a verification record must delete the photograph attached to it.

Django never removes FileField storage on delete, so the row going and the file going are
two separate acts. If only the row goes, the photograph stays in the bucket permanently:
every sweep that strips media works from the rows, so a file whose row is gone cannot be
found again except by enumerating the storage prefix. These are body photographs, and the
reviewer pressing Delete means the record and its evidence.

The cascade and queryset paths strip media before the rows go, and are pinned elsewhere
(``apps/accounts/test_delete_account_audit.py``, ``apps/accounts/test_verification_self_delete.py``).
What is pinned here is the per-instance delete: the review page's button, the Django admin's,
the shell.
"""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.team.models import RaceReadyRecord


def _record_with_photo(user, verify_type="height", **kwargs):
    """Create a record carrying a real stored file.

    Returns:
        The record, its file already written to storage.

    """
    record = RaceReadyRecord.objects.create(
        user=user,
        verify_type=verify_type,
        media_type="photo",
        status=RaceReadyRecord.Status.VERIFIED,
        height=180,
        **kwargs,
    )
    record.media_file = SimpleUploadedFile("evidence.jpg", b"not-a-real-jpeg", content_type="image/jpeg")
    record.save(update_fields=["media_file"])
    return record


@pytest.mark.django_db
def test_deleting_a_record_deletes_its_photo(user) -> None:
    """The row and the file go together -- the whole point of the override."""
    record = _record_with_photo(user)
    storage, path = record.media_file.storage, record.media_file.name
    assert storage.exists(path)

    record.delete()

    assert not RaceReadyRecord.objects.filter(pk=record.pk).exists()
    assert not storage.exists(path)


@pytest.mark.django_db
def test_the_review_page_delete_button_takes_the_photo_with_it(client, superuser, user) -> None:
    """The live path a reviewer actually uses, not just the model call underneath it."""
    record = _record_with_photo(user)
    storage, path = record.media_file.storage, record.media_file.name
    client.force_login(superuser)

    response = client.post(
        reverse("team:verification_record_detail", kwargs={"pk": record.pk}),
        {"action": "delete"},
    )

    assert response.status_code == 302
    assert not RaceReadyRecord.objects.filter(pk=record.pk).exists()
    assert not storage.exists(path)


@pytest.mark.django_db
def test_a_storage_failure_does_not_leave_the_record_behind(user) -> None:
    """The deletion was asked for; a bucket that will not answer must not veto it.

    Same rule as the rider's own delete. The orphaned path is logged instead, which is the
    only trace left of a file nothing points at any more.
    """
    record = _record_with_photo(user)

    def _explode(self) -> bool:
        raise OSError("storage unavailable")

    with patch.object(RaceReadyRecord, "delete_media_file", _explode), patch("apps.team.models.logfire") as log:
        record.delete()

    assert not RaceReadyRecord.objects.filter(pk=record.pk).exists()
    assert log.error.call_args[1]["orphaned_file"].startswith("race_ready/")


@pytest.mark.django_db
def test_a_record_with_no_file_deletes_cleanly(user, verification_factory) -> None:
    """Evidence can be a URL instead, and there is nothing in storage to remove."""
    record = verification_factory(user, "height", height=180)
    assert not record.media_file

    record.delete()

    assert not RaceReadyRecord.objects.filter(pk=record.pk).exists()
