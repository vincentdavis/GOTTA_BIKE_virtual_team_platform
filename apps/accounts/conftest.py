"""Shared fixtures for driving a Discord login in accounts tests.

Discord is mocked at the two HTTP boundaries allauth and the adapter actually use --
``requests.Session.request`` for the OAuth token exchange and profile fetch, ``httpx.get`` in
``apps.accounts.adapters`` for the guild list -- so everything between them (allauth's
callback view, ``pre_social_login``, the guild check, the login stages) runs for real.
"""

from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import requests
from constance.test import override_config
from django.core.cache import cache
from django.test import RequestFactory

TEST_GUILD_ID = 424242424242424242
GUILDS_URL = "https://discord.com/api/v10/users/@me/guilds"
TOKEN_URL = "https://discord.com/api/oauth2/token"  # noqa: S105 -- a URL, not a secret
PROFILE_URL = "https://discord.com/api/users/@me"


@pytest.fixture
def guild_id():
    """Return the team's Discord server id that the Discord-login fixtures configure."""
    return TEST_GUILD_ID


@pytest.fixture
def callback_request():
    """Build a Discord callback request whose message store is a mock the test can read.

    ``callback_request.error_text()`` joins every message queued on it.
    """
    request = RequestFactory().get("/accounts/discord/login/callback/")
    request._messages = MagicMock()
    request.error_text = lambda: " ".join(str(call.args[1]) for call in request._messages.add.call_args_list)
    return request


@pytest.fixture
def guilds_response():
    """Build the ``httpx.Response`` Discord gives for ``GET /users/@me/guilds``.

    Returns:
        ``make(guild_ids=(), *, status=200, body=None, headers=None)``; ``body`` (bytes or
        str) replaces the JSON list built from ``guild_ids``.

    """

    def make(guild_ids=(), *, status=200, body=None, headers=None):
        request = httpx.Request("GET", GUILDS_URL)
        if body is not None:
            return httpx.Response(status, content=body, headers=headers, request=request)
        payload = [{"id": str(gid), "name": f"guild {gid}"} for gid in guild_ids]
        return httpx.Response(status, json=payload, headers=headers, request=request)

    return make


@pytest.fixture
def make_sociallogin(db):
    """Build an unsaved allauth ``SocialLogin`` for a Discord account, as the callback would.

    Returns:
        ``make(discord_id, *, verified=True, email=None, token="tok", user=None, process="login")``.

    """
    from allauth.socialaccount.models import SocialAccount, SocialLogin, SocialToken
    from django.contrib.auth import get_user_model

    def make(discord_id, *, verified=True, email=None, token="tok", user=None, process="login"):  # noqa: S107
        extra_data = {
            "id": str(discord_id),
            "username": f"rider{discord_id}",
            "global_name": None,
            "avatar": None,
            "email": email or f"rider{discord_id}@example.test",
            "verified": verified,
        }
        account = SocialAccount(provider="discord", uid=str(discord_id), extra_data=extra_data)
        if user is None:
            # populate_user's throwaway: unsaved, carrying the incoming id.
            user = get_user_model()(username=extra_data["username"], discord_id=str(discord_id))
        sociallogin = SocialLogin(user=user, account=account)
        sociallogin.token = SocialToken(token=token) if token else None
        sociallogin.state = {"process": process}
        return sociallogin

    return make


def _requests_json(payload, status=200):
    """Build a ``requests.Response`` carrying JSON, as allauth's OAuth client reads it.

    Returns:
        The response.

    """
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    response.headers["content-type"] = "application/json"
    return response


@pytest.fixture
def discord_app(settings):
    """Configure a Discord OAuth app in settings, as the production env vars do."""
    providers = copy.deepcopy(settings.SOCIALACCOUNT_PROVIDERS)
    providers["discord"]["APP"] = {"client_id": "test-client", "secret": "test-secret"}
    settings.SOCIALACCOUNT_PROVIDERS = providers


@pytest.fixture
def discord_login(db, discord_app, guilds_response):
    """Drive a full Discord login through allauth's real login and callback views.

    Returns:
        ``login(client, discord_id, *, guild_ids=(TEST_GUILD_ID,), guilds=None, email=None,
        verified=True, configured_guild_id=TEST_GUILD_ID)`` returning the callback response,
        with the guild-list mock as ``response.guild_get``. ``guilds`` (an ``httpx.Response``
        or an exception) replaces the guild-list answer built from ``guild_ids``;
        ``configured_guild_id`` is what Constance ``GUILD_ID`` holds during the callback.

    """
    cache.clear()

    def login(
        client,
        discord_id,
        *,
        guild_ids=(TEST_GUILD_ID,),
        guilds=None,
        email=None,
        verified=True,
        configured_guild_id=TEST_GUILD_ID,
    ):
        profile = {
            "id": str(discord_id),
            "username": f"rider{discord_id}",
            "global_name": f"Rider {discord_id}",
            "avatar": None,
            "discriminator": "0",
            "email": email or f"rider{discord_id}@example.test",
            "verified": verified,
        }

        def fake_requests(method, url, *args, **kwargs):
            if method == "POST" and url == TOKEN_URL:
                return _requests_json({"access_token": "discord-token", "token_type": "Bearer", "expires_in": 600})
            if method == "GET" and url == PROFILE_URL:
                return _requests_json(profile)
            pytest.fail(f"unexpected outbound request {method} {url}")

        start = client.get("/accounts/discord/login/")
        if start.status_code != 302:
            pytest.fail(f"Discord login did not redirect to Discord: {start.status_code}")
        state = parse_qs(urlparse(start["Location"]).query)["state"][0]

        guild_answer = guilds if guilds is not None else guilds_response(guild_ids)
        guild_kwargs = {"side_effect": guild_answer} if isinstance(guild_answer, Exception) else {}
        with (
            override_config(GUILD_ID=configured_guild_id, DISCORD_BOT_TOKEN=""),
            patch.object(requests.Session, "request", side_effect=fake_requests),
            patch("apps.accounts.adapters.httpx.get", return_value=guild_answer, **guild_kwargs) as guild_get,
        ):
            response = client.get("/accounts/discord/login/callback/", {"code": "abc", "state": state})
        response.guild_get = guild_get
        return response

    return login
