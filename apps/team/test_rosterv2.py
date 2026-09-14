"""Guards on the card roster's gate while the page itself is being built.

The page is reachable only by typing its URL, which is the point: it will read a cache that
holds riders who never registered here, so the gate has to be right before the cards exist.
These tests pin the gate and the header's two facts; the field allow-list gets its own tests
when there is a card to leak through.
"""

import pytest
from django.urls import reverse

from conftest import _make_user


@pytest.mark.django_db
def test_a_signed_out_visitor_is_sent_to_login(client):
    response = client.get(reverse("team:rosterv2"))

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_a_signed_in_non_member_is_refused(client, user_model):
    """Not a redirect: team_member is the same gate the roster it replaces uses."""
    client.force_login(_make_user(user_model, username="outsider"))

    assert client.get(reverse("team:rosterv2")).status_code == 403


@pytest.mark.django_db
def test_a_team_member_gets_the_page(auth_client):
    response = auth_client.get(reverse("team:rosterv2"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Team Roster" in body
    # Scoped to the notice, and to the href rather than the URL anywhere: the sidebar links the
    # real roster on every page, and the link's own label is that path, so both looser forms of
    # this assertion pass with the link broken.
    notice = body.split("Under construction.", 1)[1].split("</div>", 1)[0]
    assert f'href="{reverse("team:roster")}"' in notice


@pytest.mark.django_db
def test_the_header_counts_the_riders_on_the_roster_and_dates_the_sync(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")
    roster_rider(zwid=1002, name="Bo Racer")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "2 riders" in body
    assert "stats synced" in body


@pytest.mark.django_db
def test_the_header_counts_riders_not_cached_rows(auth_client, roster_rider, rider_profile_factory):
    """Nothing purges the cache, so it keeps riders who left. The header must not count them."""
    roster_rider(zwid=1001, name="Ada Racer")
    rider_profile_factory(zwid=1002, name="Departed Rider")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "1 rider " in body
    assert "Departed Rider" not in body


@pytest.mark.django_db
def test_an_empty_roster_says_so_rather_than_claiming_a_sync(auth_client):
    """Zero riders with a 'synced just now' line would read as a working sync with no team."""
    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert "0 riders" in body
    assert "no rider stats yet" in body
    assert "stats synced" not in body


@pytest.mark.django_db
def test_the_factory_builds_a_row_through_the_real_mapping(rider_profile_factory):
    """If store_profiles stops filling these, every roster test built on it should fail here first."""
    rider = rider_profile_factory(zwid=2002, name="Cy Racer", velo=1600.0, wkg_20min=4.1, days_since_race=3)

    assert rider.name == "Cy Racer"
    assert rider.velo == pytest.approx(1600.0)
    assert rider.age == "Vet"
    assert rider.payload["power"]["curve_wkg"]["1200"] == pytest.approx(4.1)
    assert rider.peak_ratings == pytest.approx({"max30": 1620.0, "max90": 1660.0})
    # The metres-in-a-km-named-key trap, exercised by anything that builds a card.
    assert rider.lifetime_distance_km == pytest.approx(48_200)
    assert rider.last_race_at is not None
    # Present in the row, so the allow-list tests later have something real to hold back.
    assert rider.weight_kg == pytest.approx(72.0)
    assert rider.height_cm == pytest.approx(178.0)
