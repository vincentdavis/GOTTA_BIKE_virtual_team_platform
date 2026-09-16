"""The Discord login adapter logs ids, never names, a birth year or exception text.

Every login by an existing rider used to send their first name, last name and birth year to
Logfire, and several calls carried the Discord username. The guild-check failures logged
``str(e)``, which quotes the request and, for a 401, is scrubbed whole in production.
Fixtures (``discord_login``, ``guilds_response``) live in ``apps/accounts/conftest.py``.
"""

from unittest.mock import patch

import httpx
import pytest
from allauth.socialaccount.models import SocialAccount

DISCORD_ID = "700000000000000321"
FORBIDDEN_NAMES = {"first_name", "last_name", "birth_year", "discord_username", "error", "exception"}


def _logged_kwargs(fake_logfire):
    return [call.kwargs for call in fake_logfire.mock_calls if call.kwargs]


@pytest.fixture
def existing_rider(user_model):
    """Build a rider who has signed in before, with a filled-in profile.

    Returns:
        The rider.

    """
    rider = user_model.objects.create_user(
        username="returning",
        discord_id=DISCORD_ID,
        discord_username="returning-handle",
        first_name="Ottoline",
        last_name="Quennell",
        birth_year=1987,
        email=f"rider{DISCORD_ID}@example.test",
    )
    SocialAccount.objects.create(user=rider, provider="discord", uid=DISCORD_ID, extra_data={"id": DISCORD_ID})
    return rider


@pytest.mark.django_db
def test_a_returning_riders_login_logs_ids_only(client, discord_login, existing_rider):
    with patch("apps.accounts.adapters.logfire") as fake_logfire:
        discord_login(client, DISCORD_ID)

    logged = _logged_kwargs(fake_logfire)
    updating = [kw for kw in logged if kw.get("user_id") == existing_rider.pk and kw.get("discord_id") == DISCORD_ID]
    assert updating, "the existing-rider update should still be logged, by id"
    assert not [name for kwargs in logged for name in kwargs if name in FORBIDDEN_NAMES]
    text = str(fake_logfire.mock_calls)
    for value in ("Ottoline", "Quennell", "1987", "returning-handle", f"rider{DISCORD_ID}"):
        assert value not in text


@pytest.mark.django_db
def test_a_new_riders_signup_logs_no_username(client, discord_login):
    with patch("apps.accounts.adapters.logfire") as fake_logfire:
        discord_login(client, "700000000000000654")

    logged = _logged_kwargs(fake_logfire)
    assert logged
    assert not [name for kwargs in logged for name in kwargs if name in FORBIDDEN_NAMES]
    assert "rider700000000000000654" not in str(fake_logfire.mock_calls)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "guilds",
    [
        httpx.ConnectError("cannot reach https://discord.com/api/v10/users/@me/guilds"),
        httpx.Response(
            401,
            json={"message": "401: Unauthorized"},
            request=httpx.Request("GET", "https://discord.com/api/v10/users/@me/guilds"),
        ),
    ],
    ids=["unreachable", "unauthorized"],
)
def test_a_failed_guild_check_logs_the_type_and_status_not_the_message(client, discord_login, guilds):
    with patch("apps.accounts.adapters.logfire") as fake_logfire:
        discord_login(client, DISCORD_ID, guilds=guilds)

    failures = [call.kwargs for call in fake_logfire.error.call_args_list]
    assert failures
    for kwargs in failures:
        assert "error" not in kwargs
        assert kwargs["discord_id"] == DISCORD_ID
        assert "error_type" in kwargs
    assert "Unauthorized" not in str(fake_logfire.mock_calls)
    assert "discord.com" not in str(fake_logfire.mock_calls)
