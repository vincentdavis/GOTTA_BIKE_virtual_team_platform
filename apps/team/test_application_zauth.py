"""Tests for the Zwift OAuth (zauth) path on the public membership application.

An application has no User yet, so the service is keyed by the application UUID.
These also pin that such a connection can never leak into the per-user reconcile.
"""

import pytest
from django.urls import reverse

from apps.team import views
from apps.team.models import MembershipApplication


@pytest.fixture
def application(db):
    return MembershipApplication.objects.create(discord_id="123456789", discord_username="applicant")


def _connect_url(app):
    return reverse("team:application_zauth_connect", args=[app.pk])


# --- connect -----------------------------------------------------------------


@pytest.mark.django_db
def test_connect_redirects_to_consent_keyed_by_application_uuid(client, application, monkeypatch):
    seen = {}

    def fake_authorize(user_id, return_url, **kwargs):
        seen.update(user_id=user_id, return_url=return_url)
        return "https://zwift.example/consent?state=abc"

    monkeypatch.setattr("apps.zwift.client.get_authorize_url", fake_authorize)

    resp = client.post(_connect_url(application))

    assert resp.status_code == 302
    assert resp["Location"] == "https://zwift.example/consent?state=abc"
    assert seen["user_id"] == str(application.pk)  # UUID, not a user PK
    assert str(application.pk) in seen["return_url"]


@pytest.mark.django_db
def test_connect_returns_to_the_application_when_the_service_fails(client, application, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_authorize_url", lambda *a, **kw: None)

    resp = client.post(_connect_url(application))

    assert resp.status_code == 302
    assert str(application.pk) in resp["Location"]


@pytest.mark.django_db
def test_connect_refuses_once_the_application_is_no_longer_editable(client, application, monkeypatch):
    application.status = "approved"
    application.save(update_fields=["status"])
    called = []
    monkeypatch.setattr("apps.zwift.client.get_authorize_url", lambda *a, **kw: called.append(1))

    resp = client.post(_connect_url(application))

    assert resp.status_code == 302
    assert called == []  # never reaches the service


# --- status sync -------------------------------------------------------------


@pytest.mark.django_db
def test_sync_stamps_zwift_id_when_connected(application, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": True, "zwid": "4242"})

    assert views._sync_application_zauth(application) is True

    application.refresh_from_db()
    assert application.zwift_id == "4242"
    assert application.zwift_verified is True


@pytest.mark.parametrize("bad_zwid", [None, "", "abc", "0"])
@pytest.mark.django_db
def test_sync_refuses_without_a_usable_zwid(application, monkeypatch, bad_zwid):
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": True, "zwid": bad_zwid})

    assert views._sync_application_zauth(application) is False

    application.refresh_from_db()
    assert application.zwift_verified is False
    assert application.zwift_id == ""


@pytest.mark.parametrize("status", [{"connected": False}, None])
@pytest.mark.django_db
def test_sync_is_a_noop_when_not_connected_or_unavailable(application, monkeypatch, status):
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: status)

    assert views._sync_application_zauth(application) is False
    application.refresh_from_db()
    assert application.zwift_verified is False


@pytest.mark.django_db
def test_public_page_picks_up_the_connection_on_return(client, application, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": True, "zwid": "777"})

    client.get(reverse("team:application_public", args=[application.pk]))

    application.refresh_from_db()
    assert application.zwift_verified is True
    assert application.zwift_id == "777"


# --- isolation from the per-user reconcile -----------------------------------


@pytest.mark.django_db
def test_reconcile_ignores_application_uuid_connections(application, user_model, monkeypatch):
    """An application's connection must never be mistaken for a platform user's."""
    from apps.zwift import verification

    u = user_model.objects.create_user(username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth")
    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [{"user_id": str(application.pk), "zwid": "4242"}],
    )

    result = verification.reconcile_all()

    u.refresh_from_db()
    assert result["connected"] == 0  # the UUID row is skipped outright
    assert result["granted"] == 0
