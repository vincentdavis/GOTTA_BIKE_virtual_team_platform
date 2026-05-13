"""Tests for RaceReadyRecord expiration / validity properties."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.team.models import RaceReadyRecord


@pytest.mark.django_db
def test_validity_days_reads_constance(verification_factory, user) -> None:
    weight_full = verification_factory(user, "weight_full")
    weight_light = verification_factory(user, "weight_light")
    height = verification_factory(user, "height")
    power = verification_factory(user, "power")
    assert weight_full.validity_days == 120
    assert weight_light.validity_days == 30
    assert height.validity_days == 0
    assert power.validity_days == 365


@pytest.mark.django_db
def test_expires_date_none_when_not_verified(verification_factory, user) -> None:
    record = verification_factory(user, "weight_full", status=RaceReadyRecord.Status.PENDING)
    assert record.expires_date is None


@pytest.mark.django_db
def test_expires_date_none_when_record_date_missing(verification_factory, user) -> None:
    record = verification_factory(user, "weight_full")
    record.record_date = None
    record.save()
    assert record.expires_date is None


@pytest.mark.django_db
def test_expires_date_none_when_validity_zero(verification_factory, user) -> None:
    height = verification_factory(user, "height", days_ago=5000)
    assert height.expires_date is None
    assert height.is_expired is False
    assert height.days_remaining is None


@pytest.mark.django_db
def test_expires_date_computes_from_record_date(verification_factory, user) -> None:
    record = verification_factory(user, "weight_full", days_ago=10)
    today = timezone.now().date()
    expected = (today - timedelta(days=10)) + timedelta(days=120)
    assert record.expires_date == expected


@pytest.mark.django_db
def test_is_expired_false_within_window(verification_factory, user) -> None:
    record = verification_factory(user, "weight_light", days_ago=29)
    assert record.is_expired is False


@pytest.mark.django_db
def test_is_expired_true_past_window(verification_factory, user) -> None:
    record = verification_factory(user, "weight_light", days_ago=31)
    assert record.is_expired is True


@pytest.mark.django_db
def test_is_expired_false_for_pending(verification_factory, user) -> None:
    record = verification_factory(
        user,
        "weight_full",
        status=RaceReadyRecord.Status.PENDING,
        days_ago=9999,
    )
    assert record.is_expired is False


@pytest.mark.django_db
def test_days_remaining_positive_when_valid(verification_factory, user) -> None:
    record = verification_factory(user, "weight_full", days_ago=20)
    assert record.days_remaining == 100


@pytest.mark.django_db
def test_days_remaining_negative_when_expired(verification_factory, user) -> None:
    record = verification_factory(user, "weight_light", days_ago=45)
    assert record.days_remaining == -15


@pytest.mark.django_db
def test_validity_status_strings(verification_factory, user) -> None:
    pending = verification_factory(user, "power", status=RaceReadyRecord.Status.PENDING)
    valid = verification_factory(user, "power", days_ago=10)
    expired = verification_factory(user, "weight_light", days_ago=60)
    never_expires = verification_factory(user, "height", days_ago=10)
    assert pending.validity_status == "Not verified"
    assert valid.validity_status.startswith("Valid (")
    assert expired.validity_status == "Expired"
    assert never_expires.validity_status == "Never expires"
