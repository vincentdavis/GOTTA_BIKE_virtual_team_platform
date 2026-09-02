"""Tests for the official Zwift Racing Profile card on the own-profile page.

It used to render on the public profile too. That page now shows one consolidated card fed by
RiderProfile instead, so the coverage here is the own-profile render plus a guard that the
public page no longer makes the live call.

The zauth service call is patched at the ``apps.zwift.client`` boundary so no
real network traffic happens.
"""

import pytest
from django.urls import reverse

_PROFILE = {
    "zwift_user_id": "41c49fb6-3a6a-41a5-a0e5-1ac65ceec060",
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
def test_public_profile_makes_no_live_racing_profile_call(auth_client, user_model, monkeypatch):
    """The public profile was consolidated onto the cached RiderProfile row.

    Asserted on the CALL rather than on the rendered output. An output check passes for the
    wrong reason here: the replacement card renders its own "zMAP / VO2max" row, so any string
    the old partial emitted is either absent for unrelated reasons or shared with the new card.
    Spying on the client boundary tests the thing that actually matters -- that viewing a
    teammate no longer costs a per-render call to zauth.
    """
    target = user_model.objects.create_user(username="target", email="target@example.test")
    calls = []

    def _spy(user_id):
        calls.append(user_id)
        return dict(_PROFILE)

    monkeypatch.setattr("apps.zwift.client.get_racing_profile", _spy)

    resp = auth_client.get(reverse("accounts:public_profile", args=[target.pk]))

    assert resp.status_code == 200
    assert calls == [], f"public profile still called zauth for {calls}"


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


# --- Recent activities window -----------------------------------------------

_ACTIVITY_STATS = {
    "stats": {
        "days": 30,
        "count": 2,
        "sports": {"CYCLING": 1, "RUNNING": 1},
        "total_distance_m": 35000,
        "total_duration_s": 5400,
    },
    "activities": [
        {
            "activity_id": "a1",
            "name": "Morning Ride",
            "sport": "CYCLING",
            "start_date_time": "2026-07-24T06:00:00Z",
            "distance_m": 30000,
            "duration_s": 3600,
        },
        {
            "activity_id": "a2",
            "name": "Evening Run",
            "sport": "RUNNING",
            "start_date_time": "2026-07-23T18:00:00Z",
            "distance_m": 5000,
            "duration_s": 1800,
        },
    ],
}


@pytest.mark.django_db
def test_public_profile_shows_recent_activities(auth_client, user_model, monkeypatch):
    target = user_model.objects.create_user(username="rider", email="rider@example.test")
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)
    monkeypatch.setattr("apps.zwift.client.get_activity_stats", lambda user_id, days=30: dict(_ACTIVITY_STATS))

    resp = auth_client.get(reverse("accounts:public_profile", args=[target.pk]))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Recent Activities" in body
    assert "Morning Ride" in body
    assert "30.0 km" in body  # per-activity distance
    assert "35.0 km total" in body  # aggregate distance
    assert "1h 30m total" in body  # aggregate duration 5400s
    assert "Cycling: 1" in body and "Running: 1" in body


@pytest.mark.django_db
def test_public_profile_omits_activities_when_none(auth_client, user_model, monkeypatch):
    target = user_model.objects.create_user(username="rider2", email="rider2@example.test")
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)
    monkeypatch.setattr("apps.zwift.client.get_activity_stats", lambda user_id, days=30: None)

    resp = auth_client.get(reverse("accounts:public_profile", args=[target.pk]))

    assert resp.status_code == 200
    assert "Recent Activities" not in resp.content.decode()


@pytest.mark.django_db
def test_fetch_activity_window_formats_km_and_duration(monkeypatch, user):
    from apps.accounts.views import _fetch_activity_window

    monkeypatch.setattr("apps.zwift.client.get_activity_stats", lambda user_id, days=30: dict(_ACTIVITY_STATS))

    window = _fetch_activity_window(user)

    assert window["count"] == 2
    assert window["total_distance_km"] == pytest.approx(35.0)
    assert window["total_duration"] == "1h 30m"
    assert window["activities"][0]["distance_km"] == pytest.approx(30.0)
    assert window["activities"][0]["duration"] == "1h 00m"


@pytest.mark.django_db
def test_fetch_activity_window_none_when_service_returns_none(monkeypatch, user):
    from apps.accounts.views import _fetch_activity_window

    monkeypatch.setattr("apps.zwift.client.get_activity_stats", lambda user_id, days=30: None)

    assert _fetch_activity_window(user) is None


# --- zauth official OAuth status line (own profile) --------------------------


def _stub_zauth(monkeypatch, status):
    """Patch the client so the zauth status line has data; keep other calls inert."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: status is not None)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda user_id: status)
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)


@pytest.mark.django_db
def test_profile_shows_zauth_connected_line(client, user, monkeypatch):
    _stub_zauth(
        monkeypatch,
        {
            "connected": True,
            "zwid": "555",
            "zwift_user_id": "41c49fb6-3a6a-41a5-a0e5-1ac65ceec060",
            "connected_at": "2026-07-01T10:00:00Z",
        },
    )
    client.force_login(user)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert "Zwift Official Auth" in body
    assert "555" in body  # zwid
    assert "1ac65ceec060" in body  # last part of the UUID
    assert "2026-07-01" in body  # connected-since date


@pytest.mark.django_db
def test_profile_shows_zauth_connect_link_when_not_connected(client, user, monkeypatch):
    _stub_zauth(monkeypatch, {"connected": False, "zwid": None, "zwift_user_id": None, "connected_at": None})
    client.force_login(user)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert "Zwift Official Auth" in body
    assert reverse("zwift:zauth") in body  # link to the connect page


@pytest.mark.django_db
def test_profile_hides_zauth_line_when_unconfigured(client, user, monkeypatch):
    _stub_zauth(monkeypatch, None)  # is_configured -> False
    client.force_login(user)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert "Zwift Official Auth" not in body
