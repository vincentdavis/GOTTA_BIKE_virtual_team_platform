"""The Compliance tool says where to finish an unfinished erasure, reason by reason.

It used to send the admin to "the file paths in the deletion log entry" whatever went wrong,
including when the only thing left was a registration's Zwift link, which no file path
names. The audit now carries the registration id, and the message names what to look up.
Service calls are patched at the ``apps.zwift.client`` boundary.
"""

from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.accounts.services import delete_user_account
from apps.team.models import MembershipApplication
from apps.team.services import ZWIFT_LINK_NOT_RELEASED_MESSAGE
from apps.zwift.client import DisconnectOutcome

DISCORD_ID = "900000000000000042"


@pytest.fixture
def rider(user_model):
    return user_model.objects.create_user(username="erasing_rider", discord_id=DISCORD_ID)


@pytest.fixture
def registration(db):
    return MembershipApplication.objects.create(
        discord_id=DISCORD_ID,
        discord_username="erasing",
        status=MembershipApplication.Status.APPROVED,
        zwift_id="4242",
        zwift_verified=True,
    )


def _purge_result(*, failed=0, files=None):
    return {"considered": failed, "purged": 0, "failed": failed, "failed_files": files or []}


def _delete(admin_authed_client, rider):
    response = admin_authed_client.post(
        reverse("compliance_delete_user"),
        {"user_id": rider.pk, "confirmation": rider.username},
        follow=True,
    )
    return " ".join(str(m) for m in get_messages(response.wsgi_request))


@pytest.mark.django_db
def test_an_unreleased_registration_link_names_the_registration(admin_authed_client, rider, registration):
    with (
        patch("apps.zwift.client.is_configured", return_value=True),
        patch("apps.zwift.client.disconnect_link", return_value=DisconnectOutcome.NO_LINK),
        patch("apps.zwift.client.list_connections", return_value=None),  # the service cannot be asked
    ):
        text = _delete(admin_authed_client, rider)

    assert "did not finish" in text
    assert str(registration.pk) in text
    assert "registration id" in text
    assert "file path" not in text


@pytest.mark.django_db
def test_the_audit_keeps_the_registration_id(rider, registration):
    with (
        patch("apps.zwift.client.is_configured", return_value=True),
        patch("apps.zwift.client.disconnect_link", return_value=DisconnectOutcome.NO_LINK),
        patch("apps.zwift.client.list_connections", return_value=None),
    ):
        audit = delete_user_account(rider)

    assert audit["membership_application_ids"] == [str(registration.pk)]
    assert audit["application_zwift_links_failed"] == 1
    assert audit["account_zwift_link_failed"] is False
    assert not [name for name in audit if "auth" in name and name != "zauth_disconnected"]


@pytest.mark.django_db
def test_the_accounts_own_link_names_the_user_id(admin_authed_client, rider):
    rider_pk = rider.pk
    with (
        patch("apps.zwift.client.is_configured", return_value=True),
        patch("apps.zwift.client.disconnect_link", return_value=DisconnectOutcome.FAILED),
    ):
        text = _delete(admin_authed_client, rider)

    assert f"under user id {rider_pk}" in text
    assert "file path" not in text
    assert "registration id" not in text


@pytest.mark.django_db
def test_left_over_files_still_point_at_their_paths(admin_authed_client, rider):
    with patch(
        "apps.team.services.purge_user_verification_media", return_value=_purge_result(failed=1, files=["x.jpg"])
    ):
        text = _delete(admin_authed_client, rider)

    assert "file paths are under orphaned_media_files" in text
    assert "User account deleted, but the erasure did not finish" in text
    assert "Zwift service" not in text


@pytest.mark.django_db
def test_a_clean_erasure_says_nothing_about_follow_up(admin_authed_client, rider):
    text = _delete(admin_authed_client, rider)

    assert "Deleted the account" in text
    assert "did not finish" not in text


def test_the_registration_delete_warning_names_both_causes():
    assert "could not be reached" in ZWIFT_LINK_NOT_RELEASED_MESSAGE
    assert "not configured" in ZWIFT_LINK_NOT_RELEASED_MESSAGE
