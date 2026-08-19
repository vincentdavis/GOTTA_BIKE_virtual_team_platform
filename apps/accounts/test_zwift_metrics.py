"""Local mirror of the zauth service's zFTP/zMAP metrics.

The service serves these one rider at a time, so anything that needs them for a whole
roster reads the copy on ``User`` instead. These tests cover the mirror task and the
W/kg derivation. The zauth client is patched at its module boundary -- no real HTTP.
"""

from decimal import Decimal

import pytest

from apps.accounts.tasks import refresh_zwift_racing_metrics

_PROFILE = {"z_ftp": 248.0, "z_map": 340.0, "weight_in_grams": 66000}


@pytest.mark.django_db
def test_wkg_divides_by_the_weight_zwift_used(user) -> None:
    """The denominator is the metrics-time weight, not the rider's self-reported one."""
    user.z_ftp = Decimal("248.0")
    user.z_map = Decimal("340.0")
    user.z_metrics_weight_grams = 66000

    assert user.z_ftp_wkg == pytest.approx(3.7576, abs=1e-4)
    assert user.z_map_wkg == pytest.approx(5.1515, abs=1e-4)


@pytest.mark.django_db
def test_wkg_is_none_when_either_side_is_missing(user) -> None:
    user.z_ftp = Decimal("248.0")
    user.z_metrics_weight_grams = None
    assert user.z_ftp_wkg is None

    user.z_metrics_weight_grams = 66000
    user.z_ftp = None
    assert user.z_ftp_wkg is None


@pytest.mark.django_db
def test_refresh_falls_back_to_per_user_fetch_on_an_old_service(user, monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: [{"user_id": str(user.pk)}])
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: dict(_PROFILE))

    result = refresh_zwift_racing_metrics.func()
    user.refresh_from_db()

    assert result["updated"] == 1
    assert result["per_user_fetches"] == 1   # no z_ftp key in the row -> one call each
    assert user.z_ftp == Decimal("248.0")
    assert user.z_map == Decimal("340.0")
    assert user.z_metrics_weight_grams == 66000
    assert user.z_metrics_updated_at is not None


@pytest.mark.django_db
def test_refresh_keeps_last_known_values_when_a_fetch_fails(user, monkeypatch) -> None:
    """A service blip must not silently un-qualify a rider from their squad."""
    user.z_ftp = Decimal("300.0")
    user.z_metrics_weight_grams = 70000
    user.save(update_fields=["z_ftp", "z_metrics_weight_grams"])

    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: [{"user_id": str(user.pk)}])
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda uid: None)

    result = refresh_zwift_racing_metrics.func()
    user.refresh_from_db()

    assert result["failed"] == 1
    assert user.z_ftp == Decimal("300.0")


@pytest.mark.django_db
def test_refresh_skips_cleanly_when_service_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)
    assert refresh_zwift_racing_metrics.func()["status"] == "skipped"


@pytest.mark.django_db
def test_refresh_reads_the_bulk_row_without_any_per_user_call(user, monkeypatch) -> None:
    """The whole point of the bulk fields: one HTTP call for the entire roster."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: [
        {"user_id": str(user.pk), "z_ftp": 248.0, "z_map": 340.0, "weight_in_grams": 66000},
    ])

    def _boom(uid):
        msg = "per-user fetch must not happen when the bulk row carries the metrics"
        raise AssertionError(msg)

    monkeypatch.setattr("apps.zwift.client.get_racing_profile", _boom)

    result = refresh_zwift_racing_metrics.func()
    user.refresh_from_db()

    assert result["per_user_fetches"] == 0
    assert user.z_ftp == Decimal("248.0")
    assert user.z_ftp_wkg == pytest.approx(3.7576, abs=1e-4)


@pytest.mark.django_db
def test_an_all_null_bulk_row_leaves_the_last_known_values(user, monkeypatch) -> None:
    """Stale beats blank.

    Blanking on a transient upstream gap would reject every rider from every
    power-bounded squad at once.
    """
    user.z_ftp = Decimal("300.0")
    user.z_metrics_weight_grams = 70000
    user.save(update_fields=["z_ftp", "z_metrics_weight_grams"])

    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: [
        {"user_id": str(user.pk), "z_ftp": None, "z_map": None, "weight_in_grams": None},
    ])

    result = refresh_zwift_racing_metrics.func()
    user.refresh_from_db()

    assert result["skipped"] == 1
    assert result["per_user_fetches"] == 0   # key present -> never falls back
    assert user.z_ftp == Decimal("300.0")
