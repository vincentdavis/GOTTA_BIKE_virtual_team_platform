"""The page a refused Discord sign-in lands on: which account, which checks failed, what to do.

Driven through allauth's real login and callback views (``discord_login`` in
``apps/accounts/conftest.py``), with Discord mocked only at the HTTP boundary. Who gets in at
all is ``test_guild_membership_check.py``'s business; this file is about what a refused rider
is told, and that they are told nothing about anybody else.
"""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from constance.test import override_config
from django.urls import reverse
from django.utils import timezone

from apps.accounts.login_help import SESSION_KEY, TTL
from apps.accounts.models import BlockedDiscordId

DISCORD_ID = "700000000000006465"


def _help_page(client) -> str:
    return client.get(reverse("login_help")).content.decode()


@pytest.mark.django_db
def test_a_non_member_sees_the_account_they_used_and_why(client, discord_login):
    with override_config(DISCORD_URL="https://discord.gg/coalition", GUILD_NAME="The Coalition"):
        response = discord_login(client, DISCORD_ID, guild_ids=[111, 222])
        body = _help_page(client)

    assert response["Location"] == reverse("login_help")
    # Which account: the usual cause is the wrong one signed in at discord.com.
    assert f"Rider {DISCORD_ID}" in body
    assert f"@rider{DISCORD_ID}" in body
    assert "ID ending 6465" in body
    assert "is in 2 servers" in body
    assert 'href="https://discord.gg/coalition"' in body
    assert "Not met" in body
    assert "Met</span>" in body  # the email check passed and says so
    # Never the email address, even though Discord sent it.
    assert f"rider{DISCORD_ID}@example.test" not in body


@pytest.mark.django_db
def test_both_problems_show_from_one_attempt(client, discord_login):
    """The checks used to stop at the first failure, so a second one cost another attempt."""
    discord_login(client, DISCORD_ID, guild_ids=[111], verified=False)
    body = _help_page(client)

    assert body.count("Not met") == 2
    assert "verify your email" in body


@pytest.mark.django_db
def test_an_unverified_email_on_its_own(client, discord_login, guild_id):
    response = discord_login(client, DISCORD_ID, guild_ids=[guild_id], verified=False)
    body = _help_page(client)

    assert response["Location"] == reverse("login_help")
    assert body.count("Not met") == 1
    assert "User Settings &rarr; My Account" in body


@pytest.mark.django_db
def test_a_member_of_no_servers_is_told_so(client, discord_login):
    discord_login(client, DISCORD_ID, guild_ids=[])

    assert "isn&rsquo;t in any servers" in _help_page(client)


@pytest.mark.django_db
def test_a_discord_outage_says_it_could_not_check(client, discord_login):
    discord_login(client, DISCORD_ID, guilds=httpx.ReadTimeout("slow"))
    body = _help_page(client)

    assert "Couldn&rsquo;t check" in body
    assert "answer when we checked" in body


@pytest.mark.django_db
def test_a_rate_limit_says_to_wait(client, discord_login, guilds_response):
    discord_login(client, DISCORD_ID, guilds=guilds_response(status=429, body=b"slow down"))

    assert "limiting requests" in _help_page(client)


@pytest.mark.django_db
def test_an_unset_guild_id_is_owned_as_our_problem(client, discord_login):
    """It is a configuration fault here; the page must not send the rider off to fix it."""
    discord_login(client, DISCORD_ID, configured_guild_id=0)

    assert "switched off on our side" in _help_page(client)


@pytest.mark.django_db
def test_a_blocked_account_gets_no_diagnosis(client, discord_login, user_model):
    """Refused first and silently, back on the login page, with nothing stored to show."""
    BlockedDiscordId.objects.create(discord_id=DISCORD_ID)

    response = discord_login(client, DISCORD_ID, guild_ids=[111])

    assert response["Location"] == reverse("account_login")
    assert SESSION_KEY not in client.session
    body = _help_page(client)
    assert f"rider{DISCORD_ID}" not in body
    assert "To sign in, your Discord account needs to" in body
    response.guild_get.assert_not_called()


@pytest.mark.django_db
def test_a_direct_visit_explains_the_requirements(client):
    body = _help_page(client)

    assert "To sign in, your Discord account needs to" in body
    assert "You signed in to Discord as" not in body


@pytest.mark.django_db
def test_an_expired_diagnosis_is_dropped_not_shown(client, discord_login):
    discord_login(client, DISCORD_ID, guild_ids=[111])
    session = client.session
    session[SESSION_KEY]["at"] = (timezone.now() - TTL - timedelta(seconds=1)).isoformat()
    session.save()

    body = _help_page(client)

    assert f"rider{DISCORD_ID}" not in body
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_a_successful_sign_in_clears_an_earlier_diagnosis(client, discord_login, guild_id):
    """On a shared browser the next visitor must not see the last person's account."""
    discord_login(client, DISCORD_ID, guild_ids=[111])
    assert SESSION_KEY in client.session

    discord_login(client, DISCORD_ID, guild_ids=[guild_id])

    assert "_auth_user_id" in client.session
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_names_from_discord_are_escaped(client):
    session = client.session
    session[SESSION_KEY] = {
        "username": "<b>handle</b>",
        "display_name": "<script>alert(1)</script>",
        "id_tail": "1234",
        "guild": "not_member",
        "guild_count": 1,
        "email_verified": True,
        "at": timezone.now().isoformat(),
    }
    session.save()

    body = _help_page(client)

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;b&gt;handle&lt;/b&gt;" in body


@pytest.mark.django_db
@pytest.mark.parametrize("discord_url", ["", "#", "javascript:alert(1)"])
def test_no_join_link_without_an_http_invite(client, discord_login, discord_url):
    with override_config(DISCORD_URL=discord_url):
        discord_login(client, DISCORD_ID, guild_ids=[111])
        body = _help_page(client)

    assert "Join the server" not in body
    # The whole page, footer included: both go through gotta_bike_platform.url_utils.http_url.
    assert "javascript:" not in body


@pytest.mark.django_db
def test_the_invite_and_the_server_name_are_escaped(client, discord_login):
    with override_config(DISCORD_URL="https://discord.gg/x?a=1&b=2", GUILD_NAME="<b>Team</b>"):
        discord_login(client, DISCORD_ID, guild_ids=[111])
        body = _help_page(client)

    assert 'href="https://discord.gg/x?a=1&amp;b=2"' in body
    assert "&lt;b&gt;Team&lt;/b&gt;" in body
    assert "<b>Team</b>" not in body


@pytest.mark.django_db
def test_a_cancel_on_discords_screen_is_called_a_cancel(client, discord_app):
    """The old copy called every refusal on Discord's side an unverified email."""
    start = client.get("/accounts/discord/login/")
    state = parse_qs(urlparse(start["Location"]).query)["state"][0]

    response = client.get("/accounts/discord/login/callback/", {"error": "access_denied", "state": state})

    assert response["Location"] == reverse("login_help")
    body = _help_page(client)
    assert "pressed <strong>Cancel</strong>" in body
    assert "not verified" not in body


@pytest.mark.django_db
def test_an_unexplained_discord_error_carries_its_code(client, discord_app):
    start = client.get("/accounts/discord/login/")
    state = parse_qs(urlparse(start["Location"]).query)["state"][0]

    client.get("/accounts/discord/login/callback/", {"error": "server_error", "state": state})

    body = _help_page(client)
    assert "Something went wrong talking to Discord" in body
    assert "<code>unknown</code>" in body


@pytest.mark.django_db
def test_the_page_logs_what_it_explained_not_who(client, discord_login):
    discord_login(client, DISCORD_ID, guild_ids=[111])

    with patch("gotta_bike_platform.views.logfire") as log:
        _help_page(client)

    kwargs = log.info.call_args.kwargs
    assert kwargs["diagnosed"] is True
    assert kwargs["guild"] == "not_member"
    assert not any("rider" in str(value) for value in kwargs.values())
