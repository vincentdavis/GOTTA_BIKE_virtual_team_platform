"""Guards on fetching, mapping and evicting cached rider profiles.

Two things here are easy to get wrong and expensive to notice late: the retention anchor,
which is derived rather than given, and the boundary against verification state, which this
sync must not cross.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from constance.test import override_config
from django.utils import timezone

from apps.rider_data import client, services
from apps.rider_data.models import RiderProfile
from apps.rider_data.tasks import purge_rider_profiles, sync_rider_profiles
from conftest import _make_user


def _doc(zwid=1001, **over):
    doc = {
        "zwid": zwid,
        "zwift_user_id": "uuid-1",
        "identity": {"name": "Ada Racer", "gender": "female", "country": "GB", "age": "40-44"},
        "physical": {"weight_kg": 61.2, "height_cm": 170.0},
        "power": {"ftp": 240.0, "zftp": 245.0, "curve_w": {"5": 900}},
        "category": {"open": "B", "women": "A", "racing": "Gold"},
        "ratings": {"velo": 1580.0, "zwift_racing_score": 420.0, "rating_max30": 1600.0},
        "phenotype": {"value": "Sprinter", "scores": {"sprinter": 80}},
        "handicaps": {"flat": 1.1, "rolling": 0.4, "hilly": -0.6, "mountainous": -1.4},
        "clubs": {
            "current": {"id": 77, "name": "The Coalition", "source": "roster"},
            "known": [
                {"id": 77, "name": "The Coalition", "last_seen": "2026-08-01", "race_count": 12},
                {"id": 9, "name": "Old Club", "last_seen": "2025-02-14", "race_count": 3},
            ],
        },
        "sources": {"zwiftpower": {"present": True, "fetched_at": "2026-08-30T10:00:00Z"}},
        "has_account": {"zwift_api": True, "zwiftpower": True, "zwiftracing": False},
    }
    doc.update(over)
    return doc


# --- the retention anchor ------------------------------------------------------------


def test_last_race_is_the_latest_across_all_known_clubs():
    """clubs.known[].last_seen is Max(event_date) per club, so the rider's is the max of those."""
    assert services.last_race_from(_doc()).date().isoformat() == "2026-08-01"


def test_a_rider_with_no_club_history_has_no_last_race():
    """Null means we have no race history, not that they are inactive — the purge relies on it."""
    assert services.last_race_from(_doc(clubs={"current": None, "known": []})) is None


def test_a_date_without_a_time_is_not_dropped():
    """The clubs block carries dates; a naive parse would discard them and break retention."""
    doc = _doc(clubs={"current": None, "known": [{"id": 1, "last_seen": "2026-03-09"}]})
    parsed = services.last_race_from(doc)
    assert parsed is not None
    assert timezone.is_aware(parsed)


# --- mapping -------------------------------------------------------------------------


@pytest.mark.django_db
def test_promoted_columns_are_filled_and_the_rest_stays_in_payload():
    services.store_profiles([_doc()])
    row = RiderProfile.objects.get(zwid=1001)

    assert row.name == "Ada Racer"
    assert row.category_racing == "Gold"
    assert row.phenotype_value == "Sprinter"
    assert row.club_name == "The Coalition"
    assert row.last_race_at is not None

    # Not columns — reachable only through payload.
    assert row.payload["handicaps"]["flat"] == pytest.approx(1.1)
    assert row.payload["power"]["curve_w"]["5"] == 900
    assert row.payload["ratings"]["rating_max30"] == pytest.approx(1600.0)


@pytest.mark.django_db
def test_a_second_sync_updates_rather_than_duplicates():
    services.store_profiles([_doc()])
    result = services.store_profiles([_doc(identity={"name": "Ada Renamed"})])

    assert result == {"created": 0, "updated": 1, "skipped": 0}
    assert RiderProfile.objects.count() == 1
    assert RiderProfile.objects.get(zwid=1001).name == "Ada Renamed"


@pytest.mark.django_db
def test_a_document_without_a_zwid_is_skipped_not_crashed():
    result = services.store_profiles([{"identity": {"name": "No Id"}}])
    assert result["skipped"] == 1
    assert RiderProfile.objects.count() == 0


# --- the boundary this sync must not cross -------------------------------------------


@pytest.mark.django_db
def test_the_sync_never_touches_verification_state(user_model):
    """Connection status moves onto this source as its own step, deliberately.

    zwift_connection.status reports whether a Zwift account exists service-wide, not whether
    the rider is still linked to us — so driving verification off it would mark departed
    riders as verified, and verification gates Race Verified status and Discord roles.
    """
    rider = _make_user(user_model, username="verified_rider", zwid=1001)
    rider.zwid_verified = True
    rider.zwid_verification_method = "zauth"
    rider.save()

    doc = _doc(zwift_connection={"status": "disconnected", "disconnected_at": "2026-01-01T00:00:00Z"})
    with (
        patch.object(client, "fetch_profiles", return_value=[doc]),
        patch.object(client, "is_configured", return_value=True),
    ):
        sync_rider_profiles.func()

    rider.refresh_from_db()
    assert rider.zwid_verified is True, "the sync must not write verification state"
    assert rider.zwid_verification_method == "zauth"


# --- fetching ------------------------------------------------------------------------


def test_nothing_is_requested_without_a_key():
    with patch.object(client, "is_configured", return_value=False):
        assert client.fetch_profiles([1, 2, 3]) == []


def test_an_empty_request_is_refused_rather_than_fetching_everything():
    """No zwids and no app filter would ask the service for its entire rider table."""
    with patch.object(client, "is_configured", return_value=True), patch("httpx.post") as post:
        assert client.fetch_profiles([]) == []
        post.assert_not_called()


def test_one_failed_chunk_does_not_lose_the_others():
    """A partial refresh leaves rows stale, which fetched_at shows. Losing the run does not."""
    import httpx

    ok = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: [_doc(1)]})()
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "_BATCH_SIZE", 1),
        patch("httpx.post", side_effect=[ok, httpx.ConnectError("boom")]),
    ):
        assert len(client.fetch_profiles([1, 2])) == 1


# --- eviction ------------------------------------------------------------------------


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=365)
def test_a_current_member_is_never_evicted(user_model):
    """Losing membership demotes a rider; it does not delete them. Members stay regardless."""
    from apps.accounts.models import GuildMember

    member = _make_user(user_model, username="still_here", zwid=2002)
    GuildMember.objects.create(discord_id="1", username="still_here", user=member, date_left=None)

    services.store_profiles([_doc(2002)])
    RiderProfile.objects.filter(zwid=2002).update(fetched_at=timezone.now() - timedelta(days=900))

    purge_rider_profiles.func()

    assert RiderProfile.objects.filter(zwid=2002).exists()


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=365)
def test_a_departed_rider_outside_the_window_is_evicted(user_model):
    from apps.accounts.models import GuildMember

    gone = _make_user(user_model, username="departed", zwid=3003)
    GuildMember.objects.create(
        discord_id="2", username="departed", user=gone, date_left=timezone.now() - timedelta(days=400)
    )

    services.store_profiles([_doc(3003)])
    RiderProfile.objects.filter(zwid=3003).update(fetched_at=timezone.now() - timedelta(days=900))

    purge_rider_profiles.func()

    assert not RiderProfile.objects.filter(zwid=3003).exists()


@pytest.mark.django_db
def test_a_freshly_synced_row_is_never_evicted_at_the_default_window(user_model):
    """The anchor makes this inherently safe on the day it ships.

    Every row the sync writes has fetched_at = now, so nothing can be evicted until a rider
    has gone unrefreshed for the whole window. There is no moment where turning this on
    deletes a backlog.
    """
    services.store_profiles([_doc(4004)])

    result = purge_rider_profiles.func()

    assert result["deleted"] == 0
    assert RiderProfile.objects.filter(zwid=4004).exists()


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=0)
def test_zero_still_disables_the_sweep(user_model):
    services.store_profiles([_doc(4005)])
    RiderProfile.objects.filter(zwid=4005).update(fetched_at=timezone.now() - timedelta(days=9999))

    assert purge_rider_profiles.func()["deleted"] == 0
