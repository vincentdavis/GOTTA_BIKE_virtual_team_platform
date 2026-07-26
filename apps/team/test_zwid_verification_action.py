"""Tests for the admin ZWID verify/reject action.

Covers the zauth-migration provenance stamping and, on reject, the service-side
disconnect that stops the hourly reconcile from re-verifying the user.
"""

import pytest
from django.urls import reverse


@pytest.fixture
def admin_client_(client, superuser):
    client.force_login(superuser)
    return client


def _url(target):
    return reverse("team:zwid_verification_action", args=[target.pk])


@pytest.mark.django_db
def test_admin_verify_stamps_admin_method_and_timestamp(admin_client_, user):
    resp = admin_client_.post(_url(user), {"action": "verify", "zwid": "4242"})

    user.refresh_from_db()
    assert resp.status_code == 200
    assert user.zwid == 4242
    assert user.zwid_verified is True
    assert user.zwid_verification_method == "admin"
    assert user.zwid_verified_at is not None


@pytest.mark.django_db
def test_admin_reject_clears_provenance_and_disconnects_zauth(admin_client_, user_model, monkeypatch):
    target = user_model.objects.create_user(
        username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth"
    )
    calls = []
    monkeypatch.setattr("apps.zwift.client.disconnect", lambda uid: calls.append(uid) or True)

    admin_client_.post(_url(target), {"action": "reject"})

    target.refresh_from_db()
    assert calls == [str(target.pk)]  # service link severed, so reconcile cannot re-grant
    assert target.zwid is None
    assert target.zwid_verified is False
    assert target.zwid_verification_method == ""
    assert target.zwid_verified_at is None


@pytest.mark.django_db
def test_admin_reject_still_applies_when_the_service_is_unreachable(admin_client_, user_model, monkeypatch):
    """A failed disconnect must not block the admin's decision (it is logged instead)."""
    target = user_model.objects.create_user(
        username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth"
    )
    monkeypatch.setattr("apps.zwift.client.disconnect", lambda uid: False)

    admin_client_.post(_url(target), {"action": "reject"})

    target.refresh_from_db()
    assert target.zwid_verified is False
    assert target.zwid_verification_method == ""


@pytest.mark.django_db
def test_admin_reject_of_a_legacy_user_also_attempts_disconnect(admin_client_, user_model, monkeypatch):
    """Local method may lag the service, so the disconnect is unconditional."""
    target = user_model.objects.create_user(
        username="l", zwid=111, zwid_verified=True, zwid_verification_method="legacy"
    )
    calls = []
    monkeypatch.setattr("apps.zwift.client.disconnect", lambda uid: calls.append(uid) or False)

    admin_client_.post(_url(target), {"action": "reject"})

    target.refresh_from_db()
    assert calls == [str(target.pk)]
    assert target.zwid_verified is False
    assert target.zwid_verification_method == ""
