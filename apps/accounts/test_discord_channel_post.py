"""Posting to a Discord channel: what becomes of a message, and what is logged when it fails.

Discord answers 403 both when the bot cannot see a channel and when it can see it but may not
post there; only the ``code`` in its error body tells them apart, so that is what is kept.
"""

from unittest.mock import patch

import httpx
import pytest
from constance.test import override_config

from apps.accounts import discord_service
from apps.accounts.discord_service import ChannelPostResult, post_discord_channel_message, send_discord_channel_message

pytestmark = pytest.mark.django_db  # Constance keeps the bot token in the database

CHANNEL = 887000111222333444
REAL_CLIENT = httpx.Client


def _discord(status: int, body: object = None, *, text: str = ""):
    """Stand in for Discord: every request gets ``status`` with a JSON ``body``, or ``text``.

    Returns:
        A patcher for the module's ``httpx.Client``.

    """

    def handler(request: httpx.Request) -> httpx.Response:
        if body is not None:
            return httpx.Response(status, json=body, request=request)
        return httpx.Response(status, text=text, request=request)

    return patch.object(
        discord_service.httpx,
        "Client",
        side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )


@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_a_refusal_keeps_discords_own_code() -> None:
    with _discord(403, {"message": "Missing Access", "code": 50001}), patch.object(discord_service, "logfire") as log:
        result = post_discord_channel_message(CHANNEL, "Week 3 is up")

    assert result == ChannelPostResult(ok=False, status_code=403, discord_code=50001)
    logged = log.error.call_args.kwargs
    assert (logged["status_code"], logged["discord_code"], logged["discord_message"]) == (403, 50001, "Missing Access")
    assert "Week 3" not in str(log.error.call_args)  # the message itself is never logged


@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_a_refusal_without_discords_error_shape_has_no_code() -> None:
    with _discord(502, text="<html>Bad gateway</html>"), patch.object(discord_service, "logfire"):
        result = post_discord_channel_message(CHANNEL, "Week 3 is up")

    assert result == ChannelPostResult(ok=False, status_code=502, discord_code=None)


@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_a_post_that_goes_through() -> None:
    with _discord(200, {"id": "1"}):
        assert post_discord_channel_message(CHANNEL, "Week 3 is up") == ChannelPostResult(ok=True, status_code=200)
        assert send_discord_channel_message(CHANNEL, "Week 3 is up") is True


@override_config(DISCORD_BOT_TOKEN="")
@pytest.mark.parametrize(("channel", "skipped"), [(CHANNEL, "no_token"), (0, "no_channel")])
def test_nothing_is_sent_without_a_token_or_a_channel(channel, skipped) -> None:
    with _discord(200, {"id": "1"}) as client:
        result = post_discord_channel_message(channel, "Week 3 is up")

    assert result == ChannelPostResult(ok=False, skipped=skipped)
    assert send_discord_channel_message(channel, "Week 3 is up") is False
    client.assert_not_called()
