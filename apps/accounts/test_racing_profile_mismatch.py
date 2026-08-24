"""Country/gender mismatch flags on the Racing Profile card.

Red means Zwift disagrees with what the rider set. Their answer is kept either way;
the flag exists so a captain can see the divergence and decide which to trust.
"""

import pytest
from django.urls import reverse

from apps.accounts.views import _fetch_racing_profile


def _patch(monkeypatch, **data) -> None:
    """Make the zauth client return a racing profile with these DTO fields."""
    from apps.zwift import client as zwift_client

    monkeypatch.setattr(zwift_client, "get_racing_profile", lambda _uid: {"data": data})


@pytest.mark.django_db
def test_matching_values_are_not_flagged(monkeypatch, user) -> None:
    user.country = "US"
    user.gender = "male"
    user.save(update_fields=["country", "gender"])
    _patch(monkeypatch, countryAlpha3="usa", male=True)

    profile = _fetch_racing_profile(user)

    assert profile["country"] == "United States of America"
    assert profile["country_mismatch"] is False
    assert profile["gender_mismatch"] is False


@pytest.mark.django_db
def test_a_disagreement_is_flagged_on_both(monkeypatch, user) -> None:
    user.country = "US"
    user.gender = "male"
    user.save(update_fields=["country", "gender"])
    _patch(monkeypatch, countryAlpha3="gbr", male=False)

    profile = _fetch_racing_profile(user)

    assert profile["country_mismatch"] is True
    assert profile["gender_mismatch"] is True


@pytest.mark.django_db
def test_a_blank_profile_field_is_not_a_disagreement(monkeypatch, user) -> None:
    """Nothing to disagree with -- and it would have been filled on connect."""
    user.country = ""
    user.gender = ""
    user.save(update_fields=["country", "gender"])
    _patch(monkeypatch, countryAlpha3="gbr", male=False)

    profile = _fetch_racing_profile(user)

    assert profile["country_mismatch"] is False
    assert profile["gender_mismatch"] is False


@pytest.mark.django_db
def test_gender_other_counts_as_a_mismatch(monkeypatch, user) -> None:
    """Zwift cannot express "other", and the team wants every divergence visible."""
    user.gender = "other"
    user.save(update_fields=["gender"])
    _patch(monkeypatch, male=True)

    assert _fetch_racing_profile(user)["gender_mismatch"] is True


@pytest.mark.django_db
def test_zwift_reporting_nothing_is_not_a_disagreement(monkeypatch, user) -> None:
    user.country = "US"
    user.gender = "male"
    user.save(update_fields=["country", "gender"])
    _patch(monkeypatch)

    profile = _fetch_racing_profile(user)

    assert profile["country"] is None
    assert profile["country_mismatch"] is False
    assert profile["gender_mismatch"] is False


@pytest.mark.django_db
def test_the_card_paints_a_mismatch_red(monkeypatch, client, team_member) -> None:
    """End to end: the flag has to survive into the template, not just the dict."""
    team_member.country = "US"
    team_member.gender = "male"
    team_member.save(update_fields=["country", "gender"])
    _patch(monkeypatch, countryAlpha3="gbr", male=False)
    client.force_login(team_member)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert "text-error" in body
    assert "United Kingdom" in body
    assert "Does not match the country on this profile" in body
