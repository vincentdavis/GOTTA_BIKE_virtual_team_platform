"""Tests for get_user_required_verification_types and get_user_verification_types."""

import pytest

from apps.team.services import (
    DEFAULT_VERIFICATION_TYPES,
    build_verify_type_options,
    get_user_required_verification_types,
    get_user_verification_types,
)


@pytest.mark.django_db
def test_required_defaults_when_no_zwid(user) -> None:
    assert get_user_required_verification_types(user) == DEFAULT_VERIFICATION_TYPES


@pytest.mark.django_db
def test_required_defaults_when_zwid_but_no_zp_rider(user) -> None:
    user.zwid = 12345
    user.save(update_fields=["zwid"])
    assert get_user_required_verification_types(user) == DEFAULT_VERIFICATION_TYPES


@pytest.mark.django_db
def test_required_defaults_when_zero_division(user, zp_team_rider_factory) -> None:
    """A zp_rider with div=0 (falsy) falls back to defaults."""
    user.zwid = 22300
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=0, divw=0)
    assert get_user_required_verification_types(user) == DEFAULT_VERIFICATION_TYPES


@pytest.mark.django_db
def test_required_cat_b(user, zp_team_rider_factory) -> None:
    user.zwid = 22301
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    assert get_user_required_verification_types(user) == ["weight_full", "height"]


@pytest.mark.django_db
def test_required_cat_a_plus(user, zp_team_rider_factory) -> None:
    user.zwid = 22302
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=5)
    assert get_user_required_verification_types(user) == ["weight_full", "height", "power"]


@pytest.mark.django_db
def test_required_cat_d_has_both_weights(user, zp_team_rider_factory) -> None:
    user.zwid = 22303
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=40)
    assert get_user_required_verification_types(user) == ["weight_full", "weight_light", "height"]


@pytest.mark.django_db
def test_required_female_uses_divw(user, zp_team_rider_factory) -> None:
    user.zwid = 22304
    user.gender = "female"
    user.save(update_fields=["zwid", "gender"])
    # div=20 (B), divw=5 (A+) — divw should win for female
    zp_team_rider_factory(zwid=user.zwid, div=20, divw=5)
    assert get_user_required_verification_types(user) == ["weight_full", "height", "power"]


@pytest.mark.django_db
def test_submittable_always_includes_power(user) -> None:
    """Anyone can submit power, even if not required by their category."""
    types = get_user_verification_types(user)
    assert "power" in types


@pytest.mark.django_db
def test_submittable_does_not_offer_weight_light_without_verified_weight_full(
    user, zp_team_rider_factory,
) -> None:
    user.zwid = 22305
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    types = get_user_verification_types(user)
    # cat B doesn't require weight_light and user hasn't verified weight_full → not offered
    assert "weight_light" not in types


@pytest.mark.django_db
def test_submittable_offers_weight_light_when_weight_full_verified(
    user, zp_team_rider_factory, verification_factory,
) -> None:
    user.zwid = 22306
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "weight_full", days_ago=5)
    types = get_user_verification_types(user)
    assert "weight_light" in types


@pytest.mark.django_db
def test_submittable_unverified_weight_full_does_not_unlock_light(
    user, zp_team_rider_factory, verification_factory,
) -> None:
    from apps.team.models import RaceReadyRecord

    user.zwid = 22307
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "weight_full", status=RaceReadyRecord.Status.PENDING)
    types = get_user_verification_types(user)
    assert "weight_light" not in types


@pytest.mark.django_db
def test_options_required_first_and_flags(user, zp_team_rider_factory) -> None:
    """Cat B: required types listed first, power optional, weight_light hidden."""
    user.zwid = 22310
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    options = build_verify_type_options(user)
    assert [o["value"] for o in options] == ["weight_full", "height", "power"]
    by_value = {o["value"]: o for o in options}
    assert by_value["weight_full"]["required"] is True
    assert by_value["height"]["required"] is True
    assert by_value["power"]["required"] is False


@pytest.mark.django_db
def test_options_validity_labels(user, zp_team_rider_factory) -> None:
    user.zwid = 22311
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    by_value = {o["value"]: o for o in build_verify_type_options(user)}
    assert by_value["weight_full"]["validity_label"] == "Valid 120 days"
    assert by_value["height"]["validity_label"] == "Never expires"
    assert by_value["power"]["validity_label"] == "Valid 365 days"


@pytest.mark.django_db
def test_options_current_status_none_and_valid(user, zp_team_rider_factory, verification_factory) -> None:
    user.zwid = 22312
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "weight_full", days_ago=5)
    by_value = {o["value"]: o for o in build_verify_type_options(user)}
    assert by_value["weight_full"]["current"]["state"] == "valid"
    assert "115 days left" in by_value["weight_full"]["current"]["text"]
    assert by_value["height"]["current"] == {"state": "none", "text": "None on file"}


@pytest.mark.django_db
def test_options_height_verified_never_expires(user, zp_team_rider_factory, verification_factory) -> None:
    user.zwid = 22313
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "height", days_ago=400, height=175)
    height = {o["value"]: o for o in build_verify_type_options(user)}["height"]["current"]
    assert height == {"state": "valid", "text": "Verified · no expiry"}


@pytest.mark.django_db
def test_options_expired_status(user, zp_team_rider_factory, verification_factory) -> None:
    user.zwid = 22314
    user.save(update_fields=["zwid"])
    zp_team_rider_factory(zwid=user.zwid, div=20)
    verification_factory(user, "power", days_ago=400, ftp=250)
    power = {o["value"]: o for o in build_verify_type_options(user)}["power"]["current"]
    assert power["state"] == "expired"
    assert power["text"].startswith("Expired")
