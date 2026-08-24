"""Teammate autocomplete search.

Riders look each other up by whichever name they know -- Discord handle, real name, or
the name Zwift shows -- but the search used to match only the ZwiftPower profile name.
"""

import pytest

from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider


@pytest.fixture
def bot_headers(db, settings) -> dict:
    """Headers satisfying DBotAuth.

    Returns:
        The request headers.

    """
    from constance import config

    config.DBOT_AUTH_KEY = "test-key"
    config.GUILD_ID = 42   # compared with int(header), so it must not be a string
    return {
        "HTTP_X_API_KEY": "test-key",
        "HTTP_X_GUILD_ID": "42",
        "HTTP_X_DISCORD_USER_ID": "1",
    }


def _rider(zwid, name, **extra) -> ZPTeamRiders:
    """Create an active ZP team rider.

    Returns:
        The rider row.

    """
    return ZPTeamRiders.objects.create(zwid=zwid, name=name, date_left=None, **extra)


def _search(client, headers, q):
    """Call the search endpoint.

    Returns:
        The results list.

    """
    resp = client.get(f"/api/dbot/search_teammates?q={q}", **headers)
    assert resp.status_code == 200, resp.content
    return resp.json()["results"]


@pytest.mark.django_db
def test_the_zwiftpower_name_still_matches(client, bot_headers) -> None:
    _rider(101, "Speedy McFast")

    results = _search(client, bot_headers, "speedy")

    assert [r["zwid"] for r in results] == [101]
    assert results[0]["alias"] is None       # the name shown IS the match


@pytest.mark.django_db
def test_a_rider_is_found_by_their_zwift_racing_name(client, bot_headers) -> None:
    _rider(101, "Speedy McFast")
    ZRRider.objects.create(zwid=101, name="Coalition Speedy")

    results = _search(client, bot_headers, "coalition")

    assert [r["zwid"] for r in results] == [101]
    assert results[0]["name"] == "Speedy McFast"      # still labelled by the ZP name
    assert results[0]["alias"] == "Coalition Speedy"  # ...plus why it matched


@pytest.mark.django_db
def test_a_rider_is_found_by_their_discord_name(client, bot_headers, user_model) -> None:
    _rider(101, "Speedy McFast")
    user_model.objects.create_user(
        username="u1", email="u1@example.test", zwid=101,
        discord_username="zoomer", discord_nickname="Zoomer|EMEA",
    )

    results = _search(client, bot_headers, "zoomer")

    assert [r["zwid"] for r in results] == [101]
    assert results[0]["alias"] == "Zoomer|EMEA"


@pytest.mark.django_db
def test_a_rider_is_found_by_their_real_name(client, bot_headers, user_model) -> None:
    _rider(101, "Speedy McFast")
    user_model.objects.create_user(
        username="u1", email="u1@example.test", zwid=101,
        first_name="Bartholomew", last_name="Higgins", discord_username="zoomer",
    )

    assert [r["zwid"] for r in _search(client, bot_headers, "higgins")] == [101]


@pytest.mark.django_db
def test_a_rider_who_left_is_not_findable_by_any_name(client, bot_headers, user_model) -> None:
    """The wider matching must not widen who counts as a teammate."""
    ZPTeamRiders.objects.create(zwid=101, name="Departed Dan", date_left="2026-01-01T00:00:00Z")
    ZRRider.objects.create(zwid=101, name="Coalition Dan")
    user_model.objects.create_user(
        username="u1", email="u1@example.test", zwid=101, discord_username="dandan")

    assert _search(client, bot_headers, "coalition") == []
    assert _search(client, bot_headers, "dandan") == []
    assert _search(client, bot_headers, "departed") == []


@pytest.mark.django_db
def test_a_non_teammate_matching_by_discord_name_is_excluded(client, bot_headers, user_model) -> None:
    """A user with a zwid but no active ZP row is not on the roster."""
    user_model.objects.create_user(
        username="outsider", email="o@example.test", zwid=999, discord_username="zoomer")

    assert _search(client, bot_headers, "zoomer") == []


@pytest.mark.django_db
def test_one_rider_matching_several_ways_appears_once(client, bot_headers, user_model) -> None:
    _rider(101, "Zoom Rider")
    ZRRider.objects.create(zwid=101, name="Zoom Racer")
    user_model.objects.create_user(
        username="u1", email="u1@example.test", zwid=101, discord_username="zoomy")

    results = _search(client, bot_headers, "zoom")

    assert [r["zwid"] for r in results] == [101]


@pytest.mark.django_db
def test_a_short_query_returns_nothing(client, bot_headers) -> None:
    _rider(101, "Speedy McFast")

    assert _search(client, bot_headers, "s") == []
