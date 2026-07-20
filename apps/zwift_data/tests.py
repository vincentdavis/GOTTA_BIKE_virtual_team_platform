"""Tests for the Zwift Speed Lab dataset sync, catalog, and routes-page endpoints."""

import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from apps.zwift_data import catalog
from apps.zwift_data.models import ZwiftDataset, ZwiftRoute, ZwiftSegment, ZwiftWorld
from apps.zwift_data.services import sync

ROUTES_DOC = {
    "count": 2,
    "worlds": ["Watopia", "London"],
    "routes": [
        {"name": "Flat Route", "world": "Watopia", "world_id": 1, "name_hash": "111", "sport": "cycling",
         "distance_km": 10.0, "ascent_m": 50, "avg_gradient_pct": 0.5, "leadin_km": 0.5, "leadin_ascent_m": 2,
         "supports_tt": True, "event_only": False, "level_locked": 0},
        {"name": "Box Hill", "world": "London", "world_id": 3, "name_hash": "222", "sport": "cycling",
         "distance_km": 5.0, "ascent_m": 120, "avg_gradient_pct": 2.4, "leadin_km": 0.0, "leadin_ascent_m": 0,
         "supports_tt": False, "event_only": True, "level_locked": 5},
    ],
}
SEGMENTS_DOC = {
    "count": 1,
    "named": 1,
    "worlds": ["Watopia"],
    "segments": [
        {"id": "-500", "name": "Sprint A", "type": "sprint", "direction": "Forward", "world": "Watopia",
         "world_id": 1, "course_id": 6, "road_id": 1, "length_m": 200, "ascent_m": 2.0, "avg_grade_pct": 1.0,
         "max_grade_pct": 2.0, "gives_powerup": True, "type_": "sprint", "route_count": 1,
         "routes": [{"name": "Flat Route", "name_hash": "111", "world_id": 1,
                     "pct_start": 0.1, "pct_end": 0.12, "start_m": 1000, "end_m": 1200, "span_m": 200}]},
    ],
}
PROFILES_DOC = {
    "count": 1,
    "profiles": {
        "1:111": {"d": [0, 500, 1000], "e": [0, 5, 10], "lat": [1.0, 1.001, 1.002],
                  "lon": [2.0, 2.001, 2.002], "leadin_m": 0, "dist_m": 1000, "ascent_m": 10,
                  "elev_min": 0, "elev_max": 10, "max_grade_pct": 2.0},
    },
}


def _make_bundle() -> bytes:
    """Build an in-memory bundle zip matching the Speed Lab layout.

    Returns:
        The zip bytes.

    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("routes.json", json.dumps(ROUTES_DOC))
        z.writestr("segments.json", json.dumps(SEGMENTS_DOC))
        z.writestr("route_profiles.json", json.dumps(PROFILES_DOC))
    return buf.getvalue()


@pytest.fixture
def synced(db, tmp_path, settings):
    """Run a sync from a stubbed bundle into a temp media root."""
    settings.MEDIA_ROOT = str(tmp_path)
    catalog.clear_cache()
    with patch.object(sync, "_download", return_value=(_make_bundle(), "Mon, 01 Jan 2026 00:00:00 GMT")):
        sync.sync_dataset()
    yield
    catalog.clear_cache()


@pytest.fixture
def racing_admin(db, user_model):
    """Create a user with the racing_admin + team_member permissions.

    Returns:
        The user.

    """
    from conftest import _make_user

    return _make_user(user_model, username="racing_admin", permissions={"racing_admin": True, "team_member": True})


@pytest.mark.django_db
def test_sync_populates_models_and_dataset(synced):
    """A sync writes worlds/routes/segments rows and stamps the dataset."""
    assert ZwiftWorld.objects.count() == 2
    assert ZwiftRoute.objects.count() == 2
    assert ZwiftSegment.objects.count() == 1
    # signed 64-bit ids survive (negative id stored)
    assert ZwiftSegment.objects.filter(segment_id=-500).exists()
    ds = ZwiftDataset.get()
    assert ds.synced_at is not None
    assert ds.routes_count == 2 and ds.segments_count == 1 and ds.worlds_count == 2
    assert ds.profiles_count == 1
    world = ZwiftWorld.objects.get(name="Watopia")
    assert world.route_count == 1 and world.segment_count == 1


@pytest.mark.django_db
def test_catalog_reads_profile_and_segments(synced):
    """The catalog serves per-route profile + segment crossings from storage."""
    profile = catalog.route_profile(1, "111")
    assert profile is not None
    assert profile["d"] == [0, 500, 1000]
    segs = catalog.route_segments(1, "111")
    assert len(segs) == 1 and segs[0]["type"] == "sprint"
    routes = catalog.segment_routes(-500)
    assert routes and routes[0]["name_hash"] == "111"


@pytest.mark.django_db
def test_sync_rejects_incomplete_bundle(db, tmp_path, settings):
    """A bundle missing a required file raises and records the error."""
    settings.MEDIA_ROOT = str(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("routes.json", json.dumps(ROUTES_DOC))
    with patch.object(sync, "_download", return_value=(buf.getvalue(), "")), pytest.raises(ValueError, match="missing"):
        sync.sync_dataset()
    assert ZwiftDataset.get().last_error


@pytest.mark.django_db
def test_route_profile_json_endpoint(synced, auth_client):
    """The profile endpoint returns the elevation arrays for a known route."""
    resp = auth_client.get(reverse("routes:profile_json", args=[1, "111"]))
    assert resp.status_code == 200
    assert resp.json()["e"] == [0, 5, 10]


@pytest.mark.django_db
def test_route_profile_json_missing(synced, auth_client):
    """An unknown route returns 404 from the profile endpoint."""
    resp = auth_client.get(reverse("routes:profile_json", args=[9, "nope"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_check_updates_forbidden_for_team_member(auth_client):
    """A plain team member cannot trigger a data re-sync."""
    resp = auth_client.post(reverse("routes:check_updates"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_check_updates_enqueues_for_racing_admin(client, racing_admin):
    """A racing_admin triggers the background sync task."""
    client.force_login(racing_admin)
    task = MagicMock()
    with patch("apps.ttt_planner.views.sync_zwift_data", task):
        resp = client.post(reverse("routes:check_updates"))
    assert resp.status_code == 302
    task.enqueue.assert_called_once()
