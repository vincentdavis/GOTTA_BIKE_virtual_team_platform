"""Guards on the account-deletion audit trail.

Account deletion used to write nothing at all -- the most destructive action in the app
left no trace, while deleting a single verification record was fully logged. These tests
hold the audit line in place, and hold the line on what it may and may not carry: the
person is asking to be forgotten, so name and email must never reach Logfire.
"""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.team.models import RaceReadyRecord


@pytest.fixture
def deleting_member(client, team_member):
    """Log a member in and give them a name and email worth not logging.

    Returns:
        The member, ready to delete their own account.

    """
    team_member.first_name = "Delible"
    team_member.last_name = "Rider"
    team_member.email = "delible@example.com"
    team_member.discord_id = "112233445566778899"
    team_member.zwid = 987654
    team_member.save()
    client.force_login(team_member)
    return team_member


def _delete(client, confirmation="Delete"):
    """POST the delete form with logfire.info patched.

    Patching the module attribute catches the media-purge service's own log line too, so
    callers pick out the call they mean with :func:`_line` rather than assuming one call.

    Returns:
        The mock standing in for ``logfire.info``, carrying the captured calls.

    """
    with patch("apps.accounts.views.logfire.info") as info:
        client.post(reverse("accounts:profile_delete"), {"confirmation": confirmation})
    return info


def _line(info, message):
    """Find the captured logfire.info call carrying ``message``.

    Returns:
        Its keyword arguments.

    Raises:
        AssertionError: If no captured call carries that message.

    """
    for call in info.call_args_list:
        if call[0] and call[0][0] == message:
            return call[1]
    raise AssertionError(f"no logfire.info call for {message!r}; got {info.call_args_list}")


@pytest.mark.django_db
def test_deletion_is_logged_with_the_keys_needed_to_reconcile_survivors(client, deleting_member):
    """discord_id and zwid key the records that outlive the account, so both must be logged."""
    info = _delete(client)

    kwargs = _line(info, "User account deleted")
    assert kwargs["user_id"] == deleting_member.pk
    assert kwargs["discord_id"] == "112233445566778899"
    assert kwargs["zwid"] == 987654


@pytest.mark.django_db
def test_deletion_log_never_carries_name_or_email(client, deleting_member):
    """The point of the deletion is to forget the person; the audit line must not undo that."""
    info = _delete(client)

    logged = str(info.call_args)
    assert "Delible" not in logged
    assert "Rider" not in logged
    assert "delible@example.com" not in logged


@pytest.mark.django_db
def test_verification_media_is_purged_before_the_rows_cascade(client, deleting_member):
    """The files must be gone from storage, not merely unreferenced by a deleted row."""
    record = RaceReadyRecord.objects.create(
        user=deleting_member,
        verify_type="height",
        media_type="photo",
        media_file=SimpleUploadedFile("evidence.jpg", b"not-a-real-jpeg", content_type="image/jpeg"),
    )
    RaceReadyRecord.objects.create(
        user=deleting_member,
        verify_type="weight_light",
        media_type="link",
        url="https://example.com/evidence",
    )

    stored_path = record.media_file.name
    storage = record.media_file.storage
    assert storage.exists(stored_path)

    info = _delete(client)

    kwargs = _line(info, "User account deleted")
    assert not storage.exists(stored_path)
    # Both records held evidence -- an upload and an external link -- and an evidence URL
    # is as much a pointer to the rider's body as the file is, so both are stripped.
    assert kwargs["media_purged"] == 2
    assert kwargs["media_purge_failed"] == 0
    assert kwargs["orphaned_media_files"] == []
    assert kwargs["verification_records"] == 2


@pytest.mark.django_db
def test_a_rejected_confirmation_is_logged_and_deletes_nothing(client, deleting_member, user_model):
    """A failed confirmation should leave a trace too, without destroying the account."""
    info = _delete(client, confirmation="nope")

    assert user_model.objects.filter(pk=deleting_member.pk).exists()
    assert _line(info, "Account deletion not confirmed")


@pytest.mark.django_db
def test_an_unreadable_blob_does_not_block_the_deletion(client, deleting_member, user_model):
    """Refusing to delete someone because one file is unreadable is the worse outcome.

    The path is logged instead, because after the rows cascade it is the only trace of a
    file that is now unreachable except by enumerating the storage prefix.
    """
    record = RaceReadyRecord.objects.create(
        user=deleting_member,
        verify_type="height",
        media_type="photo",
        media_file=SimpleUploadedFile("evidence.jpg", b"not-a-real-jpeg", content_type="image/jpeg"),
    )
    stored_path = record.media_file.name

    def _explode(self):
        raise OSError("storage unavailable")

    with patch.object(RaceReadyRecord, "delete_media_file", _explode):
        info = _delete(client)

    kwargs = _line(info, "User account deleted")
    assert not user_model.objects.filter(pk=deleting_member.pk).exists()
    assert kwargs["media_purge_failed"] == 1
    assert kwargs["orphaned_media_files"] == [stored_path]
