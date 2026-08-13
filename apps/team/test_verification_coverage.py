"""Tests for covering_records_by_type — per-verify_type verification reconciliation."""

import pytest

from apps.team.services import covering_records_by_type


@pytest.mark.django_db
def test_longer_lived_record_supersedes_same_type(verification_factory, user):
    """Two records of the same type collapse to the one with more days remaining."""
    # weight_full validity = 120 days.
    expiring = verification_factory(user, "weight_full", days_ago=119)  # 1 day left
    renewed = verification_factory(user, "weight_full", days_ago=61)  # 59 days left

    covering = covering_records_by_type([expiring, renewed])

    assert set(covering) == {"weight_full"}
    assert covering["weight_full"].pk == renewed.pk
    assert covering["weight_full"].days_remaining == 59


@pytest.mark.django_db
def test_input_order_does_not_matter(verification_factory, user):
    """The renewed record wins regardless of iteration order."""
    expiring = verification_factory(user, "weight_full", days_ago=119)
    renewed = verification_factory(user, "weight_full", days_ago=61)

    assert covering_records_by_type([renewed, expiring])["weight_full"].pk == renewed.pk
    assert covering_records_by_type([expiring, renewed])["weight_full"].pk == renewed.pk


@pytest.mark.django_db
def test_never_expiring_type_collapses_to_none_coverage(verification_factory, user):
    """A never-expiring type (height, 0-day validity) yields a single None-days covering record."""
    older = verification_factory(user, "height", days_ago=10, height=180)
    newer = verification_factory(user, "height", days_ago=1, height=180)

    covering = covering_records_by_type([older, newer])
    assert set(covering) == {"height"}
    # Never-expires -> days_remaining is None; such a type never drives an expiry warning.
    assert covering["height"].days_remaining is None


@pytest.mark.django_db
def test_distinct_types_are_all_kept(verification_factory, user):
    """Different verify_types each keep their own covering record."""
    wf = verification_factory(user, "weight_full", days_ago=110)  # 10 left
    power = verification_factory(user, "power", days_ago=358)  # 365 - 358 = 7 left

    covering = covering_records_by_type([wf, power])
    assert covering["weight_full"].pk == wf.pk
    assert covering["power"].pk == power.pk
