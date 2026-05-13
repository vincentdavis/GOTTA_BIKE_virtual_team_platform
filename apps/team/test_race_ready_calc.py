"""Tests for User.calculate_race_ready and calculate_extra_verified."""

import pytest


@pytest.mark.django_db
def test_no_verifications_is_not_race_ready(user) -> None:
    assert user.calculate_race_ready() is False


@pytest.mark.django_db
def test_default_requirements_when_no_zwid(user, verification_factory) -> None:
    """Without a zwid, defaults to weight_light + height."""
    verification_factory(user, "weight_light", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_default_requirements_when_zwid_but_no_zp_rider(user, verification_factory) -> None:
    user.zwid = 12345
    user.save(update_fields=["zwid"])
    verification_factory(user, "weight_light", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_cat_b_requires_weight_full_plus_height(user, verification_factory, zp_team_rider_factory) -> None:
    user.zwid = 22220
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "weight_full", days_ago=5)
    assert user.calculate_race_ready() is False
    verification_factory(user, "height")
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_cat_a_plus_requires_weight_full_plus_height_plus_power(
    user,
    verification_factory,
    zp_team_rider_factory,
) -> None:
    user.zwid = 22221
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=5)
    verification_factory(user, "weight_full", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is False  # missing power
    verification_factory(user, "power", days_ago=5)
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_expired_verification_blocks_race_ready(user, verification_factory, zp_team_rider_factory) -> None:
    user.zwid = 22222
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "weight_full", days_ago=200)  # weight_full validity=120 → expired
    verification_factory(user, "height")
    assert user.calculate_race_ready() is False


@pytest.mark.django_db
def test_cat_d_or_logic_weight_light_satisfies(user, verification_factory, zp_team_rider_factory) -> None:
    """Categories 40/50 list both weight types — having either satisfies."""
    user.zwid = 22223
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=40)
    verification_factory(user, "weight_light", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_cat_d_or_logic_weight_full_also_satisfies(user, verification_factory, zp_team_rider_factory) -> None:
    user.zwid = 22224
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=50)
    verification_factory(user, "weight_full", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_cat_d_or_logic_needs_at_least_one_weight(user, verification_factory, zp_team_rider_factory) -> None:
    user.zwid = 22225
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=40)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is False


@pytest.mark.django_db
def test_female_user_uses_divw(user, verification_factory, zp_team_rider_factory) -> None:
    """Female users route through divw, not div."""
    user.zwid = 22226
    user.gender = "female"
    user.save(update_fields=["zwid", "gender"])
    # divw=5 (A+) requires weight_full + height + power; div=20 should be ignored
    zp_team_rider_factory(zwid=user.zwid, div=20, divw=5)
    verification_factory(user, "weight_full", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_race_ready() is False  # missing power
    verification_factory(user, "power", days_ago=5)
    assert user.calculate_race_ready() is True


@pytest.mark.django_db
def test_rejected_records_do_not_count(user, verification_factory, zp_team_rider_factory) -> None:
    from apps.team.models import RaceReadyRecord

    user.zwid = 22227
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "weight_full", status=RaceReadyRecord.Status.REJECTED, days_ago=5)
    verification_factory(user, "height", status=RaceReadyRecord.Status.REJECTED)
    assert user.calculate_race_ready() is False


@pytest.mark.django_db
def test_extra_verified_needs_all_three_types(user, verification_factory) -> None:
    """Extra verified is independent of category; needs weight_full + height + power."""
    assert user.calculate_extra_verified() is False
    verification_factory(user, "weight_full", days_ago=5)
    verification_factory(user, "height")
    assert user.calculate_extra_verified() is False
    verification_factory(user, "power", days_ago=5)
    assert user.calculate_extra_verified() is True


@pytest.mark.django_db
def test_extra_verified_blocked_when_one_expires(user, verification_factory) -> None:
    verification_factory(user, "weight_full", days_ago=200)  # expired
    verification_factory(user, "height")
    verification_factory(user, "power", days_ago=5)
    assert user.calculate_extra_verified() is False
