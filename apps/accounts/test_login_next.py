"""Where a login lands: the page the rider asked for, else Edit Profile until it is complete.

Riders mostly reach the app from links posted in Discord (an availability sheet, an event).
Signed out, the page they asked for sends them to the login page with ``?next=``; its Discord
button used to drop that, so after Discord everyone landed on ``LOGIN_REDIRECT_URL`` ("/").
With no page to return to, an incomplete profile goes to Edit Profile: that rule sat on the
social adapter, which allauth never asks, so it had never run. Driven through allauth's real
login and callback views (``discord_login`` in ``apps/accounts/conftest.py``).
"""

from urllib.parse import quote

import pytest
from django.urls import reverse

DISCORD_ID = "300000000000000042"
SHEET = "/events/12/squads/106/availability/2eb8e92c-732b-4cfc-a7fb-08339cb828ad/"


@pytest.mark.django_db
def test_a_signed_out_rider_is_sent_to_sign_in_with_the_page_they_asked_for(client):
    response = client.get(reverse("events:my_events"))

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('account_login')}?next={quote(reverse('events:my_events'))}"


@pytest.mark.django_db
def test_the_login_page_passes_that_page_on_to_discord(client):
    page = client.get(reverse("account_login"), {"next": SHEET}).content.decode()

    assert f"{reverse('discord_login')}?process=login&amp;next={quote(SHEET, safe='')}" in page


@pytest.mark.django_db
def test_without_one_the_discord_button_carries_none(client):
    page = client.get(reverse("account_login")).content.decode()

    assert f'href="{reverse("discord_login")}?process=login"' in page


@pytest.mark.django_db
def test_signing_in_lands_on_the_page_asked_for(client, discord_login, user_model):
    # A first login: the profile is incomplete, and the page asked for still wins.
    response = discord_login(client, DISCORD_ID, next_url=SHEET)

    assert response.status_code == 302
    assert response["Location"] == SHEET
    assert int(client.session["_auth_user_id"]) == user_model.objects.get(discord_id=DISCORD_ID).pk


@pytest.mark.django_db
def test_a_returning_rider_lands_on_it_too(client, discord_login):
    discord_login(client, DISCORD_ID)
    client.logout()

    response = discord_login(client, DISCORD_ID, next_url=SHEET)

    assert response["Location"] == SHEET


@pytest.mark.django_db
def test_a_next_on_another_site_is_ignored(client, discord_login):
    """A ``next`` is kept only when it is one of our own URLs (allauth's check): no open redirect."""
    response = discord_login(client, DISCORD_ID, next_url="https://evil.example/collect")

    assert response.status_code == 302
    assert "evil.example" not in response["Location"]


@pytest.mark.django_db
def test_a_refused_rider_still_goes_to_the_help_page(client, discord_login):
    response = discord_login(client, DISCORD_ID, guild_ids=[111], next_url=SHEET)

    assert response["Location"] == reverse("login_help")


# --- With no page to return to ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_first_login_lands_on_edit_profile(client, discord_login):
    response = discord_login(client, DISCORD_ID)

    assert response["Location"] == reverse("accounts:profile_edit")


@pytest.mark.django_db
def test_an_incomplete_profile_lands_on_edit_profile_every_time(client, discord_login):
    discord_login(client, DISCORD_ID)
    client.logout()

    response = discord_login(client, DISCORD_ID)

    assert response["Location"] == reverse("accounts:profile_edit")


@pytest.mark.django_db
def test_a_complete_profile_lands_on_the_home_page(client, discord_login, user_model, complete_profile):
    discord_login(client, DISCORD_ID)
    complete_profile(user_model.objects.get(discord_id=DISCORD_ID))
    client.logout()

    response = discord_login(client, DISCORD_ID)

    assert response["Location"] == "/"
