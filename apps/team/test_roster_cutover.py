"""/team/roster/ is the card roster now, and the table it replaced is still reachable.

The cards and the table are not the same page with different styling. They are built from
different sources -- the cards from the cached ``RiderProfile`` rows zauth fills, the table
from ``get_unified_team_roster()`` over the ZwiftPower and ZwiftRacing tables -- and the
table carries distribution charts the cards do not. So the swap keeps both, and these pin
which address serves which, including the one the Discord bot mints links against.
"""

import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.team.models import RosterFilter


@pytest.mark.django_db
def test_the_roster_address_serves_the_cards(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer")

    body = auth_client.get(reverse("team:roster")).content.decode()

    assert 'class="card bg-base-100' in body
    assert "ZwiftPower Category Distribution" not in body


@pytest.mark.django_db
def test_the_table_is_still_served_with_its_charts(auth_client, zp_team_rider_factory):
    zp_team_rider_factory(zwid=4242, div=20)

    body = auth_client.get(reverse("team:roster_table")).content.decode()

    assert "<table" in body
    assert "ZwiftPower Category Distribution" in body


@pytest.mark.django_db
def test_the_table_view_is_named_as_one_and_leads_back(auth_client):
    """Landing on the table by a stale link should not look like the roster having changed."""
    body = auth_client.get(reverse("team:roster_table")).content.decode()

    assert "table view" in body
    assert f'href="{reverse("team:roster")}"' in body


@pytest.mark.django_db
def test_resetting_the_tables_filters_keeps_you_on_the_table(auth_client):
    """Reset clears the filters; it is not a way off the page."""
    body = auth_client.get(reverse("team:roster_table") + "?gender=F").content.decode()

    reset = body.split(">Reset<", 1)[0].rsplit("<a ", 1)[1]
    assert reverse("team:roster_table") in reset


@pytest.mark.django_db
def test_the_table_is_gated_like_the_cards(client, user):
    """It is the same roster; moving it must not have left it open."""
    client.force_login(user)

    assert client.get(reverse("team:roster_table")).status_code == 403


@pytest.mark.django_db
def test_the_discord_bots_channel_link_still_renders(client, zp_team_rider_factory):
    """Deliberately open, deliberately five minutes -- and it renders through the table.

    Moving the table would have broken the one roster surface that answers without a login.
    """
    zp_team_rider_factory(zwid=4242, div=20)
    roster_filter = RosterFilter.objects.create(
        id=uuid.uuid4(),
        channel_name="race-chat",
        discord_ids=["900004242"],
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    response = client.get(reverse("team:filtered_roster", args=[roster_filter.id]))

    assert response.status_code == 200
    assert "race-chat" in response.content.decode()


@pytest.mark.django_db
def test_the_sidebar_sends_everyone_to_the_cards_and_knows_it_is_there(auth_client):
    """One link, to the roster people are meant to use, lit up when they are on it.

    Asserted as one string: the href alone appears elsewhere on the page, and "menu-active"
    alone is on whichever entry happens to be current.
    """
    body = auth_client.get(reverse("team:roster")).content.decode()

    assert f'href="{reverse("team:roster")}" class="menu-active" aria-current="page"' in body


@pytest.mark.django_db
def test_the_table_still_lights_the_same_sidebar_entry(auth_client):
    """It is the roster under another view, so the nav should not go dark on it."""
    body = auth_client.get(reverse("team:roster_table")).content.decode()

    assert f'href="{reverse("team:roster")}" class="menu-active" aria-current="page"' in body
