"""The per-rider fallback in the zFTP/zMAP refresh is gated by the refresh interval.

The bulk path is one cheap call against the zauth service's own database. The fallback
is one call per rider, and that endpoint reaches Zwift itself when the service holds no
snapshot -- so at a 5-minute cadence it would multiply into a rate-limit problem.
"""

import pytest
from constance import config

from apps.accounts.tasks import refresh_zwift_racing_metrics

# @task wraps the callable, so the plain function is reached via .func.
_refresh = refresh_zwift_racing_metrics.func


@pytest.fixture
def connected(db, user_model):
    """Build a user the service reports as connected, with no inline metrics.

    Returns:
        The user.

    """
    return user_model.objects.create_user(username="r1", email="r1@example.test")


def _patch(monkeypatch, connections, profile=None) -> dict:
    """Stub the zauth client; record whether the per-rider endpoint was called.

    Returns:
        A dict with a ``calls`` counter.

    """
    from apps.zwift import client as zwift_client

    seen = {"calls": 0}
    monkeypatch.setattr(zwift_client, "is_configured", lambda: True)
    monkeypatch.setattr(zwift_client, "list_connections", lambda: connections)

    def _profile(_uid):
        seen["calls"] += 1
        return profile

    monkeypatch.setattr(zwift_client, "get_racing_profile", _profile)
    return seen


@pytest.mark.django_db
def test_a_short_interval_suppresses_the_per_rider_fallback(monkeypatch, connected) -> None:
    config.SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES = 5
    config.ZWIFT_METRICS_FALLBACK_MIN_MINUTES = 30
    # No "z_ftp" key -> the bulk path cannot serve this rider.
    seen = _patch(monkeypatch, [{"user_id": str(connected.pk)}], {"z_ftp": 250})

    result = _refresh()

    assert seen["calls"] == 0            # Zwift was never reached
    assert result["per_user_fetches"] == 0
    assert result["skipped"] == 1
    connected.refresh_from_db()
    assert connected.z_ftp is None


@pytest.mark.django_db
def test_a_long_interval_still_allows_it(monkeypatch, connected) -> None:
    """The guard is about cadence, not about disabling the fallback outright."""
    config.SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES = 60
    config.ZWIFT_METRICS_FALLBACK_MIN_MINUTES = 30
    seen = _patch(monkeypatch, [{"user_id": str(connected.pk)}], {"z_ftp": 250, "z_map": 400})

    result = _refresh()

    assert seen["calls"] == 1
    assert result["per_user_fetches"] == 1
    connected.refresh_from_db()
    assert connected.z_ftp == 250


@pytest.mark.django_db
def test_the_bulk_path_is_unaffected_by_a_short_interval(monkeypatch, connected) -> None:
    """The guard must not stop the cheap path -- that is the whole point of 5 minutes."""
    config.SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES = 5
    config.ZWIFT_METRICS_FALLBACK_MIN_MINUTES = 30
    seen = _patch(monkeypatch, [
        {"user_id": str(connected.pk), "z_ftp": 260, "z_map": 410, "weight_in_grams": 70000},
    ])

    result = _refresh()

    assert seen["calls"] == 0
    assert result["updated"] == 1
    connected.refresh_from_db()
    assert connected.z_ftp == 260
