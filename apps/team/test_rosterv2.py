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
    response = client.get(reverse("team:roster"))

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_a_signed_in_non_member_is_refused(client, user_model):
    """Not a redirect: team_member is the same gate the roster it replaces uses."""
    client.force_login(_make_user(user_model, username="outsider"))

    assert client.get(reverse("team:roster")).status_code == 403


@pytest.mark.django_db
def test_a_team_member_gets_the_page(auth_client):
    response = auth_client.get(reverse("team:roster"))

    assert response.status_code == 200
    assert "Team Roster" in response.content.decode()


@pytest.mark.django_db
def test_the_page_offers_the_table_it_replaced(auth_client):
    """The table still holds the distribution charts, so it must stay one click away.

    Asserted on the href rather than the word: the sidebar and the page both say "roster",
    so a looser assertion passes with the link missing.
    """
    body = auth_client.get(reverse("team:roster")).content.decode()

    assert f'href="{reverse("team:roster_table")}"' in body


@pytest.mark.django_db
def test_the_address_the_cards_were_built_at_still_leads_here(client, team_member):
    """The link was handed round while this was under construction."""
    client.force_login(team_member)

    response = client.get(reverse("team:rosterv2"))

    assert response.status_code == 301
    assert response["Location"] == reverse("team:roster")


@pytest.mark.django_db
def test_the_header_counts_the_riders_on_the_roster_and_dates_the_sync(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")
    roster_rider(zwid=1002, name="Bo Racer")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "2 riders" in body
    assert "stats synced" in body


@pytest.mark.django_db
def test_the_header_counts_riders_not_cached_rows(auth_client, roster_rider, rider_profile_factory):
    """Nothing purges the cache, so it keeps riders who left. The header must not count them."""
    roster_rider(zwid=1001, name="Ada Racer")
    rider_profile_factory(zwid=1002, name="Departed Rider")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "1 rider " in body
    assert "Departed Rider" not in body


@pytest.mark.django_db
def test_an_empty_roster_says_so_rather_than_claiming_a_sync(auth_client):
    """Zero riders with a 'synced just now' line would read as a working sync with no team."""
    body = auth_client.get(reverse("team:roster")).content.decode()

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


# --- when the stats cache is behind the team ------------------------------------------------


@pytest.mark.django_db
def test_a_rider_with_no_cached_stats_is_counted_even_though_they_have_no_card(
    auth_client, roster_rider, zp_team_rider_factory
):
    """The failure this exists to catch is silent: a roster that looks fine and is not.

    A card needs cached stats AND a place on the team. If the stats sync has not reached a
    rider, they simply are not on the page -- so a roster showing five of two thousand riders
    reads as a team of five. The header says both numbers instead.
    """
    roster_rider(zwid=1001, name="Ada Racer")
    zp_team_rider_factory(zwid=1002, name="Not Yet Synced")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "1 of 2 riders" in body
    assert "Not Yet Synced" not in body


@pytest.mark.django_db
def test_a_complete_roster_does_not_explain_itself(auth_client, roster_rider):
    """With nothing missing the header states one number, not two."""
    roster_rider(zwid=1001, name="Ada Racer")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert "1 rider " in body
    assert "of 1 riders" not in body
