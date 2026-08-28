"""Admin-initiated account deletion, for honouring an erasure request on someone's behalf.

Runs the same service the rider's own Delete Account page runs, so the two cannot drift.
The confirmation differs on purpose: an admin can pick the wrong person out of a long
list, which typing a fixed word would never catch.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import User


@pytest.fixture
def victim(user_model):
    """Build the account to be deleted.

    Returns:
        The user.

    """
    return user_model.objects.create_user(
        username="lapsed_rider", email="lapsed@example.test",
        first_name="Lapsed", last_name="Rider", discord_id="123", zwid=555,
    )


@pytest.mark.django_db
def test_the_section_lists_members(admin_authed_client, victim) -> None:
    """The picker has to offer the account being erased."""
    body = admin_authed_client.get(
        reverse("config_section_page", args=["compliance"])
    ).content.decode()

    assert "Delete a member&#x27;s account" in body or "Delete a member's account" in body
    assert "Lapsed Rider" in body


@pytest.mark.django_db
def test_the_confirm_page_names_the_account_and_its_effects(admin_authed_client, victim) -> None:
    """Same warning the rider would see, plus who it is about."""
    body = admin_authed_client.get(
        reverse("compliance_delete_confirm"), {"user_id": victim.pk}
    ).content.decode()

    assert "lapsed_rider" in body
    assert "What is deleted" in body
    assert "What we keep" in body


@pytest.mark.django_db
def test_the_wrong_username_does_not_delete(admin_authed_client, victim) -> None:
    """The whole point of typing it: catching a mis-picked person."""
    response = admin_authed_client.post(
        reverse("compliance_delete_user"),
        {"user_id": victim.pk, "confirmation": "Delete"},
    )

    assert response.status_code == 302
    assert User.objects.filter(pk=victim.pk).exists()


@pytest.mark.django_db
def test_the_matching_username_deletes(admin_authed_client, victim) -> None:
    """The happy path."""
    admin_authed_client.post(
        reverse("compliance_delete_user"),
        {"user_id": victim.pk, "confirmation": "lapsed_rider"},
    )

    assert not User.objects.filter(pk=victim.pk).exists()


@pytest.mark.django_db
def test_an_admin_cannot_delete_themselves_here(admin_authed_client, app_admin) -> None:
    """The view does not log the actor out, so it would leave a dead session behind."""
    response = admin_authed_client.post(
        reverse("compliance_delete_user"),
        {"user_id": app_admin.pk, "confirmation": app_admin.username},
    )

    assert response.status_code == 302
    assert User.objects.filter(pk=app_admin.pk).exists()


@pytest.mark.django_db
def test_a_plain_team_member_cannot_reach_it(auth_client, victim) -> None:
    """Irreversible deletion of someone else's account stays behind the config gate."""
    assert auth_client.get(reverse("compliance_delete_confirm"), {"user_id": victim.pk}).status_code == 403
    assert auth_client.post(
        reverse("compliance_delete_user"),
        {"user_id": victim.pk, "confirmation": victim.username},
    ).status_code == 403
    assert User.objects.filter(pk=victim.pk).exists()


@pytest.mark.django_db
def test_it_runs_the_same_deletion_as_the_self_serve_page(admin_authed_client, victim) -> None:
    """Both paths go through delete_user_account, so neither can drift from the other.

    That service is what performs the media purge and the zauth disconnect, so an erasure
    carried out on someone's behalf reaches exactly what theirs would.
    """
    from unittest.mock import patch

    with patch("apps.team.services.purge_user_verification_media") as purge:
        purge.return_value = {"purged": 0, "failed": 0, "failed_files": [], "considered": 0}
        admin_authed_client.post(
            reverse("compliance_delete_user"),
            {"user_id": victim.pk, "confirmation": "lapsed_rider"},
        )

    purge.assert_called_once()
    assert not User.objects.filter(pk=victim.pk).exists()


@pytest.mark.django_db
def test_the_audit_records_who_did_it(victim, app_admin) -> None:
    """A deletion carried out by somebody else must not look self-serve in the log."""
    from apps.accounts.services import delete_user_account

    audit = delete_user_account(victim, deleted_by=app_admin)

    assert audit["deleted_by_id"] == app_admin.pk
    assert audit["self_serve"] is False
    # Name and email stay out: the person is being forgotten.
    assert "email" not in audit
    assert audit["discord_id"] == "123"
