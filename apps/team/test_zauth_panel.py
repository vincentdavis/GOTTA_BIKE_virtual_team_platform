"""The Zwift Auth card on the verification review page.

The zauth service is patched at the ``apps.zwift.client`` boundary, so no test here
makes a real HTTP call.
"""

import pytest
from django.urls import reverse

from apps.team.zauth_panel import UNAVAILABLE_REASON, build_zauth_panel

_STATUS = {"connected": True, "zwid": "555", "connected_at": "2026-05-01T09:30:00Z"}
_PROFILE = {"weight_in_grams": 72400, "fetched_at": "2026-08-16T06:15:00Z"}


@pytest.fixture
def connected(monkeypatch):
    """Patch the client so the rider looks connected with a stored profile."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: dict(_PROFILE))


@pytest.mark.django_db
def test_connected_rider_gets_weight_and_timestamp(user, connected) -> None:
    panel = build_zauth_panel(user)

    assert panel["configured"] is True
    assert panel["connected"] is True
    assert panel["zwid"] == "555"
    assert panel["weight_kg"] == pytest.approx(72.4)
    # Parsed to datetimes so the page can format them like its other timestamps.
    assert panel["connected_at"].year == 2026
    assert panel["connected_at"].month == 5
    assert panel["weight_as_of"].hour == 6
    assert panel["weight_as_of"].minute == 15


@pytest.mark.django_db
def test_metrics_the_service_cannot_supply_are_none_not_zero(user, connected) -> None:
    """A blank or zero would read as a real measurement to a reviewer."""
    panel = build_zauth_panel(user)

    for key in ("height_cm", "weight_90d_min", "weight_90d_max", "height_90d_min", "height_90d_max"):
        assert panel[key] is None
    assert panel["unavailable_reason"] == UNAVAILABLE_REASON


@pytest.mark.django_db
def test_unconfigured_service_short_circuits_without_calling_out(user, monkeypatch) -> None:
    """No service configured must not attempt a request at all."""
    calls = []
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: calls.append(uid))

    panel = build_zauth_panel(user)

    assert panel["configured"] is False
    assert panel["connected"] is False
    assert calls == []


@pytest.mark.django_db
def test_not_connected_leaves_weight_unavailable(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": False})

    panel = build_zauth_panel(user)

    assert panel["connected"] is False
    assert panel["weight_kg"] is None


@pytest.mark.django_db
def test_a_down_service_never_breaks_the_panel(user, monkeypatch) -> None:
    """The client returns None on failure; that must read as "unknown", not "0 kg"."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: None)

    panel = build_zauth_panel(user)

    assert panel["connected"] is False
    assert panel["weight_kg"] is None


@pytest.mark.django_db
def test_connected_without_a_snapshot_yet(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: None)

    panel = build_zauth_panel(user)

    assert panel["connected"] is True
    assert panel["weight_kg"] is None
    assert panel["weight_as_of"] is None


@pytest.mark.django_db
def test_nonsense_gram_values_read_as_no_weight(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    for grams in (0, -1, None, "72400", {}):
        monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid, g=grams: {"weight_in_grams": g})
        assert build_zauth_panel(user)["weight_kg"] is None


@pytest.mark.django_db
def test_platform_verification_stamp_is_reported_separately(user_model, connected) -> None:
    """The platform's own stamp gates race-ready and can lag the live connection."""
    from django.utils import timezone

    u = user_model.objects.create_user(username="z", email="z@example.test")
    u.zwid_verified = True
    u.zwid_verification_method = u.VerificationMethod.ZAUTH
    u.zwid_verified_at = timezone.now()
    u.save(update_fields=["zwid_verified", "zwid_verification_method", "zwid_verified_at"])

    panel = build_zauth_panel(u)

    assert panel["verified_via_zauth"] is True
    assert panel["verified_at"] is not None


@pytest.mark.django_db
def test_legacy_verification_is_not_reported_as_zauth(user_model, connected) -> None:
    u = user_model.objects.create_user(username="l", email="l@example.test")
    u.zwid_verified = True
    u.zwid_verification_method = u.VerificationMethod.LEGACY
    u.save(update_fields=["zwid_verified", "zwid_verification_method"])

    assert build_zauth_panel(u)["verified_via_zauth"] is False


@pytest.mark.django_db
def test_card_renders_on_the_review_page(client, verification_factory, user_model, connected) -> None:
    reviewer = user_model.objects.create_user(
        username="rev", email="rev@example.test",
        permission_overrides={"team_member": True, "approve_verification": True},
    )
    rider = user_model.objects.create_user(username="rider", email="rider@example.test")
    record = verification_factory(rider, "weight_full", weight=74.0)
    client.force_login(reviewer)

    resp = client.get(reverse("team:verification_record_detail", args=[record.pk]))
    body = resp.content.decode()

    assert resp.status_code == 200
    assert "Zwift Auth" in body
    assert "Connected" in body
    assert "72.4 kg" in body
    assert "Weight 90d min" in body
    assert UNAVAILABLE_REASON in body


@pytest.mark.django_db
def test_card_explains_an_unconnected_rider(client, verification_factory, user_model, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": False})
    reviewer = user_model.objects.create_user(
        username="rev2", email="rev2@example.test",
        permission_overrides={"team_member": True, "approve_verification": True},
    )
    rider = user_model.objects.create_user(username="rider2", email="rider2@example.test")
    record = verification_factory(rider, "weight_full")
    client.force_login(reviewer)

    body = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "Not connected" in body
    assert "has not connected their Zwift account" in body
