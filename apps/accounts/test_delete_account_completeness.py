"""Guards that a half-finished erasure is never reported as a finished one.

Deletion deliberately does not stop when a step fails: refusing to erase somebody because
one blob is unreadable, or because the zauth service is down, is the worse outcome. The
cost of that choice is that a partial erasure and a clean one used to look identical --
same success message to the rider, same info-level log line.

A transaction is not the remedy and would make things worse: the storage purge and the
zauth call reach outside this database and cannot be rolled back, so wrapping the function
would undo the rows while leaving the blobs and the upstream link gone. What is wanted is
that the shortfall is visible, which is what these tests hold in place.
"""

from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.accounts.services import delete_user_account


@pytest.fixture
def rider(user_model):
    return user_model.objects.create_user(
        username="erasing_rider", email="erasing@example.test", discord_id="42", zwid=4242
    )


def _purge_result(*, purged=0, failed=0, files=None):
    return {"considered": purged + failed, "purged": purged, "failed": failed, "failed_files": files or []}


@pytest.mark.django_db
def test_a_clean_deletion_reports_complete(rider):
    with patch("apps.team.services.purge_user_verification_media", return_value=_purge_result(purged=2)):
        audit = delete_user_account(rider)
    assert audit["complete"] is True
    assert audit["incomplete_reasons"] == []


@pytest.mark.django_db
def test_a_rider_who_never_connected_zwift_is_not_reported_incomplete(rider):
    """disconnect() returning False means there was no link -- the ordinary case, not a failure.

    Treating that as a failure would mark almost every deletion unfinished and make the
    signal worthless.
    """
    with (
        patch("apps.team.services.purge_user_verification_media", return_value=_purge_result()),
        patch("apps.zwift.client.is_configured", return_value=True),
        patch("apps.zwift.client.disconnect", return_value=False),
    ):
        audit = delete_user_account(rider)
    assert audit["zauth_disconnected"] is False
    assert audit["complete"] is True, "no link to remove is not a failed removal"


@pytest.mark.django_db
def test_unpurgeable_media_marks_the_erasure_incomplete(rider):
    with patch(
        "apps.team.services.purge_user_verification_media",
        return_value=_purge_result(purged=1, failed=2, files=["race_ready/a.jpg", "race_ready/b.jpg"]),
    ):
        audit = delete_user_account(rider)
    assert audit["complete"] is False
    assert any("verification file" in r for r in audit["incomplete_reasons"])
    assert audit["orphaned_media_files"] == ["race_ready/a.jpg", "race_ready/b.jpg"]


@pytest.mark.django_db
def test_a_zauth_outage_marks_the_erasure_incomplete(rider):
    with (
        patch("apps.team.services.purge_user_verification_media", return_value=_purge_result()),
        patch("apps.zwift.client.is_configured", return_value=True),
        patch("apps.zwift.client.disconnect", side_effect=RuntimeError("service down")),
    ):
        audit = delete_user_account(rider)
    assert audit["complete"] is False
    assert any("Zwift link" in r for r in audit["incomplete_reasons"])


@pytest.mark.django_db
def test_an_unfinished_erasure_logs_at_error_not_info(rider):
    """It is a standing obligation, so it must be findable without knowing to look."""
    with (
        patch("apps.team.services.purge_user_verification_media", return_value=_purge_result(failed=1)),
        patch("apps.accounts.services.logfire.error") as err,
        patch("apps.accounts.services.logfire.info") as info,
    ):
        delete_user_account(rider)
    assert err.called
    assert not any(c[0] and c[0][0] == "User account deleted" for c in info.call_args_list)


@pytest.mark.django_db
def test_a_clean_erasure_still_logs_at_info(rider):
    with (
        patch("apps.team.services.purge_user_verification_media", return_value=_purge_result(purged=1)),
        patch("apps.accounts.services.logfire.info") as info,
    ):
        delete_user_account(rider)
    assert any(c[0] and c[0][0] == "User account deleted" for c in info.call_args_list)


@pytest.mark.django_db
def test_the_rider_is_not_told_deletion_succeeded_when_it_did_not(client, team_member):
    """The rider asked to be forgotten; an unqualified 'deleted' is the one wrong answer."""
    client.force_login(team_member)
    with patch(
        "apps.team.services.purge_user_verification_media", return_value=_purge_result(failed=1, files=["x.jpg"])
    ):
        response = client.post(reverse("accounts:profile_delete"), {"confirmation": "Delete"}, follow=True)
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "could not be removed" in text
    assert "Your account has been deleted." not in text


@pytest.mark.django_db
def test_the_admin_is_told_what_did_not_finish(admin_authed_client, rider):
    with patch(
        "apps.team.services.purge_user_verification_media", return_value=_purge_result(failed=3, files=["x.jpg"])
    ):
        response = admin_authed_client.post(
            reverse("compliance_delete_user"),
            {"user_id": rider.pk, "confirmation": rider.username},
            follow=True,
        )
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "did not finish" in text
    assert "3 verification file" in text
