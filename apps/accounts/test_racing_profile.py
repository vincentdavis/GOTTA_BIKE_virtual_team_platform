"""Tests for the official Zwift Racing Profile card on the profile pages.

The zauth service call is patched at the ``apps.zwift.client`` boundary so no
real network traffic happens.
"""

import pytest
from django.urls import reverse

_PROFILE = {
    "zwid": "555",
    "first_name": "Alice",
    "last_name": "Rider",
    "ftp": 250.0,
    "category": "B",
    "category_women": None,
    "racing_score": 435.0,
    "z_ftp": 248.0,
    "z_map": 340.0,
    "vo2max": 60.5,
    "weight_in_grams": 66000,
    "data": {},
    "fetched_at": "2026-07-01T10:00:00Z",
}


@pytest.mark.django_db
def test_public_profile_shows_racing_profile_when_connected(auth_client, user_model, monkeypatch):
    target = user_model.objects.create_user(username="target", email="target@example.test")
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: dict(_PROFILE))

    resp = auth_client.get(reverse("accounts:public_profile", args=[target.pk]))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Racing Score" in body  # a label only in the rendered card
    assert "435" in body  # racing score value
    assert "66.0 kg" in body  # weight derived from grams


@pytest.mark.django_db
def test_public_profile_omits_racing_profile_when_not_connected(auth_client, user_model, monkeypatch):
    target = user_model.objects.create_user(username="target2", email="target2@example.test")
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)

    resp = auth_client.get(reverse("accounts:public_profile", args=[target.pk]))

    assert resp.status_code == 200
    # "Racing Profile" also appears in an HTML comment, so assert on a
    # rendered-only label instead.
    assert "Racing Score" not in resp.content.decode()


@pytest.mark.django_db
def test_private_profile_shows_racing_profile(client, team_member, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: dict(_PROFILE))
    client.force_login(team_member)

    resp = client.get(reverse("accounts:profile"))

    assert resp.status_code == 200
    assert "Racing Score" in resp.content.decode()


@pytest.mark.django_db
def test_fetch_racing_profile_derives_weight_kg(monkeypatch, user):
    from apps.accounts.views import _fetch_racing_profile

    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: dict(_PROFILE))

    result = _fetch_racing_profile(user)

    assert result["weight_kg"] == pytest.approx(66.0)


@pytest.mark.django_db
def test_fetch_racing_profile_none_when_service_returns_none(monkeypatch, user):
    from apps.accounts.views import _fetch_racing_profile

    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)

    assert _fetch_racing_profile(user) is None
