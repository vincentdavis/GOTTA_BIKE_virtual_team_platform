"""Tests for the expiring_verifications context processor (banner data)."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory

from apps.team.context_processors import expiring_verifications


@pytest.fixture
def _clear_cache():
    # Per-user cache keys can collide across rolled-back tests when row ids are
    # reused, so start each test from an empty cache.
    cache.clear()
    yield
    cache.clear()


def _request(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


@pytest.mark.django_db
def test_anonymous_user_gets_none_without_query(_clear_cache, django_assert_num_queries):
    request = _request(AnonymousUser())
    with django_assert_num_queries(0):
        result = expiring_verifications(request)
    assert result == {"expiring_verifications": None}


@pytest.mark.django_db
def test_no_records_returns_none(_clear_cache, user):
    result = expiring_verifications(_request(user))
    assert result["expiring_verifications"] is None


@pytest.mark.django_db
def test_record_within_window_is_flagged(_clear_cache, verification_factory, user):
    # weight_full validity = 120 days; record_date 110 days ago -> 10 days left.
    verification_factory(user, "weight_full", days_ago=110)
    payload = expiring_verifications(_request(user))["expiring_verifications"]
    assert payload is not None
    assert payload["count"] == 1
    assert payload["soonest_days"] == 10
    assert payload["soonest_type"] == "Weight Full"


@pytest.mark.django_db
def test_record_outside_window_is_ignored(_clear_cache, verification_factory, user):
    # 120 - 100 = 20 days left, beyond the 15-day threshold.
    verification_factory(user, "weight_full", days_ago=100)
    assert expiring_verifications(_request(user))["expiring_verifications"] is None


@pytest.mark.django_db
def test_expired_record_is_ignored(_clear_cache, verification_factory, user):
    # Already past expiry (days_remaining negative) -> not an "expiring" warning.
    verification_factory(user, "weight_full", days_ago=130)
    assert expiring_verifications(_request(user))["expiring_verifications"] is None


@pytest.mark.django_db
def test_pending_record_is_ignored(_clear_cache, verification_factory, user):
    from apps.team.models import RaceReadyRecord

    verification_factory(user, "weight_full", days_ago=110, status=RaceReadyRecord.Status.PENDING)
    assert expiring_verifications(_request(user))["expiring_verifications"] is None


@pytest.mark.django_db
def test_soonest_record_wins_and_count_is_total(_clear_cache, verification_factory, user):
    verification_factory(user, "weight_full", days_ago=110)  # 10 days left
    verification_factory(user, "power", days_ago=358)  # 365 - 358 = 7 days left
    payload = expiring_verifications(_request(user))["expiring_verifications"]
    assert payload["count"] == 2
    assert payload["soonest_days"] == 7
    assert payload["soonest_type"] == "Power"
