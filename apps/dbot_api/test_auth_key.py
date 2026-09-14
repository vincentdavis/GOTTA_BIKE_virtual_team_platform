"""The Discord bot API key is compared in constant time.

A plain ``!=`` on a shared secret returns as soon as two bytes differ, so response
timing leaks the key one character at a time to anyone who can call the endpoint.
The empty-key case matters separately: an unconfigured ``DBOT_AUTH_KEY`` must not
authenticate a caller who also sends nothing.
"""

import pytest
from constance.test import override_config

API_KEY = "s3cret-bot-key"
GUILD_ID = 42
ENDPOINT = "/api/dbot/bot_config"


def _headers(key: str | None = API_KEY) -> dict:
    """Build bot request headers.

    Returns:
        The request headers, omitting the key header when `key` is None.

    """
    headers = {"HTTP_X_GUILD_ID": str(GUILD_ID), "HTTP_X_DISCORD_USER_ID": "1"}
    if key is not None:
        headers["HTTP_X_API_KEY"] = key
    return headers


@pytest.mark.django_db
def test_the_right_key_is_accepted(client) -> None:
    """A correct key still authenticates."""
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID):
        assert client.get(ENDPOINT, **_headers()).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "wrong",
    [
        "",
        "x",
        "S3CRET-BOT-KEY",           # case differs
        "s3cret-bot-ke",            # prefix of the real key
        "s3cret-bot-keyy",          # real key plus a byte
        "s3cret-bot-kex",           # differs in the last byte only
        "X3cret-bot-key",           # differs in the first byte only
        "s3cret-bot-key ",          # trailing whitespace
        "s3cret-bot-kéy",           # non-ascii: must be rejected, not raise
    ],
)
def test_a_wrong_key_is_rejected(client, wrong: str) -> None:
    """Every near-miss is refused, including non-ASCII (which `compare_digest` rejects on str)."""
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID):
        assert client.get(ENDPOINT, **_headers(wrong)).status_code == 401


@pytest.mark.django_db
def test_a_missing_key_header_is_rejected(client) -> None:
    """No key header at all is refused rather than raising."""
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID):
        assert client.get(ENDPOINT, **_headers(None)).status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("sent", ["", "anything", None])
def test_an_unconfigured_key_authenticates_nobody(client, sent: str | None) -> None:
    """With DBOT_AUTH_KEY unset, no request gets in -- not even an empty one."""
    with override_config(DBOT_AUTH_KEY="", GUILD_ID=GUILD_ID):
        assert client.get(ENDPOINT, **_headers(sent)).status_code == 401


@pytest.mark.django_db
def test_the_comparison_is_constant_time() -> None:
    """The key check goes through `hmac.compare_digest`, not `==`/`!=`.

    Behaviour alone cannot distinguish the two, so this pins the call itself: patch
    `hmac.compare_digest` to record that it saw the key, and fail if it never ran.
    """
    import hmac
    from unittest.mock import patch

    from django.test import Client

    from apps.dbot_api import api as dbot_api

    seen = []
    # Bind the real function first: patching `dbot_api.hmac` patches the shared
    # module object, so calling `hmac.compare_digest` inside the double would recurse.
    real_compare_digest = hmac.compare_digest

    def recording_compare_digest(a, b):  # test double
        seen.append((a, b))
        return real_compare_digest(a, b)

    with (
        override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID),
        patch.object(dbot_api.hmac, "compare_digest", recording_compare_digest),
    ):
        assert Client().get(ENDPOINT, **_headers()).status_code == 200

    assert seen, "the API key was not compared with hmac.compare_digest"
    assert seen[0] == (API_KEY.encode(), API_KEY.encode())
