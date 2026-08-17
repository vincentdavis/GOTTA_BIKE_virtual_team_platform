"""The Zwift Auth card on the verification review page.

The zauth service is patched at the ``apps.zwift.client`` boundary, so no test here
makes a real HTTP call.
"""

import pytest
from django.urls import reverse

from apps.team.zauth_panel import UNAVAILABLE_REASON, build_zauth_panel

_STATUS = {"connected": True, "zwid": "555", "connected_at": "2026-05-01T09:30:00Z"}
# Both weights present and disagreeing, as Zwift really returns them: the live
# top-level `weight` under `data`, and the frozen competitionMetrics one.
_PROFILE = {
    "weight_in_grams": 76260,          # competitionMetrics: "weight at snapshot time"
    "data": {"weight": 76203},         # live profile weight
    "fetched_at": "2026-08-16T06:15:00Z",
}


_STATS = {
    "current": {"height_in_millimeters": 1750, "weight_in_grams": 76260},
    "windows": {"90d": {
        "weight_in_grams": {"min": 74800, "max": 77100, "first": 76000, "last": 76260, "count": 7},
        "height_in_millimeters": {"min": 1750, "max": 1750, "first": 1750, "last": 1750, "count": 3},
    }},
}


@pytest.fixture(autouse=True)
def _no_stats_by_default(monkeypatch):
    """Default every test to "no history", so each opts in to stats explicitly."""
    monkeypatch.setattr("apps.zwift.client.get_profile_stats", lambda uid: None)


@pytest.fixture
def connected(monkeypatch):
    """Patch the client so the rider looks connected with a stored profile."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: dict(_PROFILE))


@pytest.fixture
def with_history(connected, monkeypatch):
    """Add a snapshot history on top of the connected fixture."""
    monkeypatch.setattr("apps.zwift.client.get_profile_stats", lambda uid: dict(_STATS))


@pytest.mark.django_db
def test_connected_rider_gets_weight_and_timestamp(user, connected) -> None:
    panel = build_zauth_panel(user)

    assert panel["configured"] is True
    assert panel["connected"] is True
    assert panel["zwid"] == "555"
    assert panel["weight_kg"] == pytest.approx(76.2)      # live profile weight
    assert panel["metrics_weight_kg"] == pytest.approx(76.3)  # the metrics snapshot one
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
    for grams in (0, -1, None, "76203", {}, True):
        monkeypatch.setattr("apps.zwift.client.get_racing_profile",
                            lambda uid, g=grams: {"data": {"weight": g}})
        assert build_zauth_panel(user)["weight_kg"] is None


@pytest.mark.django_db
def test_never_falls_back_to_the_competition_metrics_weight(user, monkeypatch) -> None:
    """A stale snapshot weight under a fresh "as of" timestamp is worse than nothing."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile",
                        lambda uid: {"weight_in_grams": 76260, "data": {}})

    panel = build_zauth_panel(user)

    assert panel["weight_kg"] is None
    assert panel["metrics_weight_kg"] == pytest.approx(76.3)


@pytest.mark.django_db
def test_prefers_a_denormalized_field_once_the_service_publishes_one(user, monkeypatch) -> None:
    """The durable path: read the service's own field ahead of the passed-through blob."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: {
        "profile_weight_in_grams": 75000, "data": {"weight": 76203}, "weight_in_grams": 76260,
    })

    assert build_zauth_panel(user)["weight_kg"] == pytest.approx(75.0)


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
    assert "76.2 kg" in body  # live weight
    # That the profile row uses the live weight rather than the metrics one is pinned by
    # test_never_falls_back_to_the_competition_metrics_weight; the metrics weight now has
    # its own row, so its absence from the page is no longer the right assertion.
    # Labels changed when the ranges became real; this test has no snapshot history, so
    # the range cells show their em dash.
    assert "Weight 90d range" in body
    assert "Height 90d range" in body


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


@pytest.mark.django_db
def test_drift_reports_movement_since_zwifts_snapshot(user, connected) -> None:
    """The gap is the signal: the rider changed weight after Zwift last recomputed."""
    panel = build_zauth_panel(user)

    # live 76.2 vs metrics 76.3 -> the rider is now lighter than Zwift raced them at.
    assert panel["weight_drift"] == "-0.1"


@pytest.mark.django_db
def test_no_drift_shown_when_the_two_weights_agree(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: {
        "weight_in_grams": 76200, "data": {"weight": 76200},
    })

    assert build_zauth_panel(user)["weight_drift"] is None


@pytest.mark.django_db
def test_drift_is_signed_both_ways(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: {
        "weight_in_grams": 75000, "data": {"weight": 76400},
    })

    assert build_zauth_panel(user)["weight_drift"] == "+1.4"


@pytest.mark.django_db
def test_no_drift_when_one_weight_is_missing(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: dict(_STATUS))
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: {"data": {"weight": 76203}})

    panel = build_zauth_panel(user)

    assert panel["metrics_weight_kg"] is None
    assert panel["weight_drift"] is None


@pytest.mark.django_db
def test_both_weights_render_as_separate_rows(client, verification_factory, user_model, connected) -> None:
    reviewer = user_model.objects.create_user(
        username="rev3", email="rev3@example.test",
        permission_overrides={"team_member": True, "approve_verification": True},
    )
    rider = user_model.objects.create_user(username="rider3", email="rider3@example.test")
    record = verification_factory(rider, "weight_full", weight=76.5)
    client.force_login(reviewer)

    body = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "Zwift profile weight" in body
    assert "Racing metrics weight" in body
    assert "76.2 kg" in body  # live
    assert "76.3 kg" in body  # metrics snapshot
    assert "-0.1 kg" in body  # drift between them


@pytest.mark.django_db
def test_height_and_ranges_come_from_the_snapshot_history(user, with_history) -> None:
    panel = build_zauth_panel(user)

    assert panel["height_cm"] == pytest.approx(175.0)
    assert panel["weight_90d_min"] == pytest.approx(74.8)
    assert panel["weight_90d_max"] == pytest.approx(77.1)
    assert panel["weight_90d_count"] == 7
    assert panel["height_90d_min"] == pytest.approx(175.0)
    assert panel["height_90d_max"] == pytest.approx(175.0)
    assert panel["height_90d_count"] == 3


@pytest.mark.django_db
def test_swing_is_flagged_once_it_could_move_a_category(user, with_history) -> None:
    """74.8 -> 77.1 is 2.3 kg, over the attention threshold."""
    panel = build_zauth_panel(user)

    assert panel["weight_90d_swing"] == pytest.approx(2.3)
    assert panel["weight_90d_swing_high"] is True


@pytest.mark.django_db
def test_a_small_swing_is_not_flagged(user, connected, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.get_profile_stats", lambda uid: {
        "windows": {"90d": {"weight_in_grams": {"min": 76100, "max": 76400, "count": 2}}},
    })

    panel = build_zauth_panel(user)

    assert panel["weight_90d_swing"] == pytest.approx(0.3)
    assert panel["weight_90d_swing_high"] is False


@pytest.mark.django_db
def test_no_history_leaves_the_ranges_unavailable(user, connected) -> None:
    """get_profile_stats returns None when the service is down or has nothing."""
    panel = build_zauth_panel(user)

    assert panel["height_cm"] is None
    assert panel["weight_90d_min"] is None
    assert panel["weight_90d_count"] == 0
    assert panel["weight_90d_swing"] is None


@pytest.mark.django_db
def test_a_metric_absent_from_the_window_stays_none(user, connected, monkeypatch) -> None:
    """Height was added to snapshots later, so older windows carry null for it."""
    monkeypatch.setattr("apps.zwift.client.get_profile_stats", lambda uid: {
        "current": {"height_in_millimeters": None},
        "windows": {"90d": {
            "weight_in_grams": {"min": 76100, "max": 76400, "count": 2},
            "height_in_millimeters": None,
        }},
    })

    panel = build_zauth_panel(user)

    assert panel["weight_90d_min"] == pytest.approx(76.1)
    assert panel["height_cm"] is None
    assert panel["height_90d_min"] is None
    assert panel["height_90d_count"] == 0


@pytest.mark.django_db
def test_malformed_stats_payloads_degrade_quietly(user, connected, monkeypatch) -> None:
    for payload in (None, {}, {"windows": None}, {"windows": {"90d": None}},
                    {"windows": {"90d": {"weight_in_grams": "nope"}}}, {"current": "nope"}):
        monkeypatch.setattr("apps.zwift.client.get_profile_stats", lambda uid, p=payload: p)
        panel = build_zauth_panel(user)
        assert panel["weight_90d_min"] is None
        assert panel["height_cm"] is None


@pytest.mark.django_db
def test_ranges_render_on_the_page(client, verification_factory, user_model, with_history) -> None:
    reviewer = user_model.objects.create_user(
        username="rev4", email="rev4@example.test",
        permission_overrides={"team_member": True, "approve_verification": True},
    )
    rider = user_model.objects.create_user(username="rider4", email="rider4@example.test")
    record = verification_factory(rider, "weight_full", weight=76.5)
    client.force_login(reviewer)

    body = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "175.0 cm" in body
    assert "74.8" in body and "77.1" in body
    assert "7 readings" in body
    assert "2.3 kg" in body  # the swing
    assert UNAVAILABLE_REASON not in body  # nothing unavailable now
