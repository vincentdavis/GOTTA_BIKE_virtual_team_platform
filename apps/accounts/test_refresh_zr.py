"""Tests for the own-profile Zwift Racing refresh control."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.zwiftracing.models import ZRRider

# Minimal Zwift Racing API payload that _map_rider_to_model understands.
API_RIDER = {
    "name": "Test Rider",
    "race": {"current": {"rating": 1234.5, "mixed": {"category": "B", "number": 2}}},
}


@pytest.fixture
def verified_user(user):
    # A user with a verified Zwift ID, ready to force_login.
    user.zwid = 12345
    user.zwid_verified = True
    user.save(update_fields=["zwid", "zwid_verified"])
    return user


# --- refresh_rider_sync (service) ------------------------------------------------


@pytest.mark.django_db
def test_refresh_rider_sync_upserts_on_success(monkeypatch) -> None:
    from apps.zwiftracing import tasks

    monkeypatch.setattr(tasks, "get_rider", lambda zwid: (200, API_RIDER))
    status, rider = tasks.refresh_rider_sync(12345)

    assert status == 200
    assert rider is not None
    assert rider.zwid == 12345
    assert rider.race_current_category == "B"
    assert ZRRider.objects.filter(zwid=12345).exists()


@pytest.mark.django_db
def test_refresh_rider_sync_returns_none_on_rate_limit(monkeypatch) -> None:
    from apps.zwiftracing import tasks

    monkeypatch.setattr(tasks, "get_rider", lambda zwid: (429, {"retryAfter": "600"}))
    status, rider = tasks.refresh_rider_sync(12345)

    assert status == 429
    assert rider is None
    assert not ZRRider.objects.filter(zwid=12345).exists()


# --- refresh_zr view -------------------------------------------------------------


@pytest.mark.django_db
def test_refresh_zr_fetches_when_no_record(client, verified_user, monkeypatch) -> None:
    from apps.zwiftracing import tasks

    calls = []
    monkeypatch.setattr(tasks, "get_rider", lambda zwid: calls.append(zwid) or (200, API_RIDER))

    client.force_login(verified_user)
    response = client.post(reverse("accounts:refresh_zr"))

    assert response.status_code == 200
    assert calls == [12345]  # the fetch happened
    rider = ZRRider.objects.get(zwid=12345)
    assert rider.race_current_category == "B"
    assert "B" in response.content.decode()


@pytest.mark.django_db
def test_refresh_zr_skips_when_recently_updated(client, verified_user, monkeypatch) -> None:
    from apps.zwiftracing import tasks

    # Existing record updated "just now" (auto_now) -> within the 1h window.
    ZRRider.objects.create(zwid=12345, name="Old", race_current_category="C")

    calls = []
    monkeypatch.setattr(tasks, "get_rider", lambda zwid: calls.append(zwid) or (200, API_RIDER))

    client.force_login(verified_user)
    response = client.post(reverse("accounts:refresh_zr"))

    assert response.status_code == 200
    assert calls == []  # guarded: no upstream fetch
    assert ZRRider.objects.get(zwid=12345).race_current_category == "C"  # unchanged


@pytest.mark.django_db
def test_refresh_zr_fetches_when_record_is_stale(client, verified_user, monkeypatch) -> None:
    from apps.zwiftracing import tasks

    ZRRider.objects.create(zwid=12345, name="Old", race_current_category="C")
    # Bypass auto_now to age the record past the 1h window.
    ZRRider.objects.filter(zwid=12345).update(date_modified=timezone.now() - timedelta(hours=2))

    calls = []
    monkeypatch.setattr(tasks, "get_rider", lambda zwid: calls.append(zwid) or (200, API_RIDER))

    client.force_login(verified_user)
    response = client.post(reverse("accounts:refresh_zr"))

    assert response.status_code == 200
    assert calls == [12345]
    assert ZRRider.objects.get(zwid=12345).race_current_category == "B"  # refreshed


@pytest.mark.django_db
def test_refresh_zr_surfaces_error_and_keeps_data_on_failed_fetch(client, verified_user, monkeypatch) -> None:
    from apps.zwiftracing import tasks

    # Existing (stale) data so the ZR card renders; a failed refresh should keep it.
    ZRRider.objects.create(zwid=12345, name="Old", race_current_category="C")
    ZRRider.objects.filter(zwid=12345).update(date_modified=timezone.now() - timedelta(hours=2))
    monkeypatch.setattr(tasks, "get_rider", lambda zwid: (429, {"retryAfter": "600"}))

    client.force_login(verified_user)
    response = client.post(reverse("accounts:refresh_zr"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Update failed" in body
    assert ZRRider.objects.get(zwid=12345).race_current_category == "C"  # preserved


@pytest.mark.django_db
def test_refresh_zr_requires_login(client) -> None:
    response = client.post(reverse("accounts:refresh_zr"))
    assert response.status_code in (302, 403)


# --- profile page renders the control --------------------------------------------


@pytest.mark.django_db
def test_profile_page_shows_refresh_button_when_stale(client, verified_user) -> None:
    ZRRider.objects.create(zwid=12345, name="Rider", race_current_category="B", race_current_rating=1000)
    ZRRider.objects.filter(zwid=12345).update(date_modified=timezone.now() - timedelta(hours=3))

    client.force_login(verified_user)
    body = client.get(reverse("accounts:profile")).content.decode()

    assert reverse("accounts:refresh_zr") in body  # active refresh button present
    assert "Updated" in body  # tooltip shows last-updated text
