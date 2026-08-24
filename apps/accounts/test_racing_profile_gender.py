"""Zwift-reported gender on the Racing Profile card.

Zwift sends a boolean `male` on the raw DTO, which has three meaningful states once
"absent" is counted -- so it is resolved to a string before it reaches the template.
Testing the boolean directly in a template would hide every rider reported as female.
"""

import pytest

from apps.accounts.views import _fetch_racing_profile


def _patch_profile(monkeypatch, payload) -> None:
    """Make the zauth client return `payload`."""
    from apps.zwift import client as zwift_client

    monkeypatch.setattr(zwift_client, "get_racing_profile", lambda _uid: payload)


@pytest.mark.django_db
def test_male_true_reads_as_male(monkeypatch, user) -> None:
    _patch_profile(monkeypatch, {"data": {"male": True}})

    assert _fetch_racing_profile(user)["gender"] == "Male"


@pytest.mark.django_db
def test_male_false_reads_as_female(monkeypatch, user) -> None:
    """The case a naive `{% if profile.male %}` would silently drop."""
    _patch_profile(monkeypatch, {"data": {"male": False}})

    assert _fetch_racing_profile(user)["gender"] == "Female"


@pytest.mark.django_db
def test_absent_stays_none_so_the_row_is_hidden(monkeypatch, user) -> None:
    """Zwift not reporting it is different from Zwift reporting female."""
    _patch_profile(monkeypatch, {"data": {}})

    assert _fetch_racing_profile(user)["gender"] is None


@pytest.mark.django_db
def test_a_payload_with_no_data_key_does_not_raise(monkeypatch, user) -> None:
    """`data` is required by the service's schema, but the card must not 500 without it."""
    _patch_profile(monkeypatch, {"category": "B"})

    assert _fetch_racing_profile(user)["gender"] is None


@pytest.mark.django_db
def test_the_card_renders_the_female_row(monkeypatch, client, team_member) -> None:
    """End to end: the value has to survive the template, not just the view."""
    from django.urls import reverse

    _patch_profile(monkeypatch, {"data": {"male": False}, "category": "B"})
    client.force_login(team_member)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert "Female" in body
    assert ">Gender<" in body
