"""Filling and flagging country/gender from a Zwift racing profile.

A rider's own answer always wins: Zwift only fills a blank field, and a disagreement
is surfaced on the profile card rather than resolved silently.
"""

import pytest

from apps.zwift import profile_fields


def _profile(**data) -> dict:
    """Wrap raw DTO fields the way the zauth service returns them.

    Returns:
        A racing-profile dict.

    """
    return {"data": data}


@pytest.mark.parametrize(
    ("alpha3", "expected"),
    [("usa", "US"), ("USA", "US"), ("gbr", "GB"), ("xxx", ""), ("", ""), (None, "")],
)
def test_country_is_converted_from_alpha3(alpha3, expected) -> None:
    """Zwift sends alpha-3; the platform stores alpha-2. Junk must not raise."""
    assert profile_fields.zwift_country(_profile(countryAlpha3=alpha3)) == expected


def test_country_survives_a_missing_payload() -> None:
    assert profile_fields.zwift_country(None) == ""
    assert profile_fields.zwift_country({}) == ""


@pytest.mark.parametrize(("male", "expected"), [(True, "male"), (False, "female"), (None, "")])
def test_gender_distinguishes_female_from_absent(male, expected) -> None:
    """False is a real answer; treating it as missing would erase every woman."""
    assert profile_fields.zwift_gender(_profile(male=male)) == expected


@pytest.mark.django_db
def test_blank_fields_are_filled(user) -> None:
    user.country = ""
    user.gender = ""
    user.save(update_fields=["country", "gender"])

    filled = profile_fields.fill_missing(user, _profile(countryAlpha3="gbr", male=False))

    user.refresh_from_db()
    assert sorted(filled) == ["country", "gender"]
    assert user.country.code == "GB"
    assert user.gender == "female"


@pytest.mark.django_db
def test_an_answer_the_rider_gave_is_never_overwritten(user) -> None:
    """The whole point: Zwift informs, it does not override."""
    user.country = "US"
    user.gender = "male"
    user.save(update_fields=["country", "gender"])

    filled = profile_fields.fill_missing(user, _profile(countryAlpha3="gbr", male=False))

    user.refresh_from_db()
    assert filled == []
    assert user.country.code == "US"
    assert user.gender == "male"


@pytest.mark.django_db
def test_one_blank_field_is_filled_without_touching_the_other(user) -> None:
    user.country = "US"
    user.gender = ""
    user.save(update_fields=["country", "gender"])

    filled = profile_fields.fill_missing(user, _profile(countryAlpha3="gbr", male=True))

    user.refresh_from_db()
    assert filled == ["gender"]
    assert user.country.code == "US"       # kept
    assert user.gender == "male"           # filled


@pytest.mark.django_db
def test_zwift_reporting_nothing_fills_nothing(user) -> None:
    user.country = ""
    user.gender = ""
    user.save(update_fields=["country", "gender"])

    assert profile_fields.fill_missing(user, _profile()) == []
    assert profile_fields.fill_missing(user, None) == []
