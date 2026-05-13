"""Tests for User.refresh_race_ready cache behavior."""

import pytest


@pytest.mark.django_db
def test_refresh_returns_calculated_values(user, verification_factory) -> None:
    verification_factory(user, "weight_light", days_ago=5)
    verification_factory(user, "height")
    race_ready, extra = user.refresh_race_ready()
    assert race_ready is True
    assert extra is False  # extra needs weight_full, not weight_light


@pytest.mark.django_db
def test_refresh_persists_new_value(user, verification_factory) -> None:
    """Cache field is written when calculation differs from stored value."""
    assert user.is_race_ready is False
    verification_factory(user, "weight_light", days_ago=5)
    verification_factory(user, "height")
    user.refresh_race_ready()
    user.refresh_from_db()
    assert user.is_race_ready is True


@pytest.mark.django_db
def test_refresh_no_save_when_unchanged(user, django_assert_num_queries) -> None:
    """When the cached value already matches the calculation, no UPDATE is issued."""
    # Fresh user: cached is_race_ready=False, calc=False → match → no save
    with django_assert_num_queries(2):
        # 2 queries: one for race_ready_records (calc), one for re-query (extra_verified)
        race_ready, extra = user.refresh_race_ready()
    assert race_ready is False
    assert extra is False


@pytest.mark.django_db
def test_refresh_extra_verified_path(user, verification_factory, zp_team_rider_factory) -> None:
    """Cat A+ user with weight_full + height + power is both race-ready and extra-verified."""
    user.zwid = 33333
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=5)
    verification_factory(user, "weight_full", days_ago=5)
    verification_factory(user, "height")
    verification_factory(user, "power", days_ago=5)
    race_ready, extra = user.refresh_race_ready()
    assert race_ready is True
    assert extra is True
    user.refresh_from_db()
    assert user.is_race_ready is True
    assert user.is_extra_verified is True


@pytest.mark.django_db
def test_refresh_handles_demotion(user, verification_factory) -> None:
    """A previously race-ready user who lets a verification expire is downgraded."""
    verification_factory(user, "weight_light", days_ago=5)
    height = verification_factory(user, "height")
    user.refresh_race_ready()
    user.refresh_from_db()
    assert user.is_race_ready is True

    # Reject the height record → no longer race ready
    from apps.team.models import RaceReadyRecord

    height.status = RaceReadyRecord.Status.REJECTED
    height.save(update_fields=["status"])

    user.refresh_race_ready()
    user.refresh_from_db()
    assert user.is_race_ready is False
