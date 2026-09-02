"""Discord rate limits are waited out rather than counted as failures.

Opening a DM channel is itself rate limited, and the expiring-verification task sends in a
loop with only a short sleep between riders. On a busy threshold day the first few sends
succeed and the rest come back 429 -- which was indistinguishable from a genuine failure, so
each one silently cost a rider their warning.

Only 429 is retried. A 403 (the rider has DMs closed) or a 404 will not improve by asking
again, and retrying those would just slow the batch down.
"""

from unittest.mock import Mock, patch

import httpx
import pytest

from apps.accounts.discord_service import _MAX_RATE_LIMIT_RETRIES, _post_with_retry


def _response(status: int, *, body: dict | None = None, headers: dict | None = None) -> Mock:
    """Build a stand-in httpx response.

    Args:
        status: HTTP status code.
        body: JSON body, or None to make .json() raise as a non-JSON response would.
        headers: Response headers.

    Returns:
        The mock response.

    """
    response = Mock(spec=httpx.Response)
    response.status_code = status
    response.headers = headers or {}
    response.json = Mock(return_value=body) if body is not None else Mock(side_effect=ValueError)
    return response


def _client(*responses) -> Mock:
    """Build a client returning the given responses in order.

    Args:
        *responses: Responses to return from successive posts.

    Returns:
        The mock client.

    """
    client = Mock(spec=httpx.Client)
    client.post = Mock(side_effect=list(responses))
    return client


def test_a_429_is_retried_and_then_succeeds():
    client = _client(_response(429, body={"retry_after": 0.2}), _response(200, body={"id": "1"}))

    with patch("apps.accounts.discord_service.time.sleep") as slept:
        result = _post_with_retry(client, "https://example.test/x")

    assert result.status_code == 200
    assert client.post.call_count == 2
    slept.assert_called_once_with(0.2)


def test_the_wait_comes_from_the_body_retry_after():
    """Discord puts fractional seconds in the body; that is the value to honour."""
    client = _client(_response(429, body={"retry_after": 1.75}), _response(200, body={}))

    with patch("apps.accounts.discord_service.time.sleep") as slept:
        _post_with_retry(client, "https://example.test/x")

    slept.assert_called_once_with(1.75)


def test_the_header_is_used_when_the_body_is_not_json():
    """Some 429s come back without a JSON body; the header still carries the interval."""
    client = _client(_response(429, headers={"Retry-After": "2"}), _response(200, body={}))

    with patch("apps.accounts.discord_service.time.sleep") as slept:
        _post_with_retry(client, "https://example.test/x")

    slept.assert_called_once_with(2.0)


def test_an_absurd_retry_after_is_capped():
    """A worker must not be pinned for an hour because Discord said so."""
    client = _client(_response(429, body={"retry_after": 3600}), _response(200, body={}))

    with patch("apps.accounts.discord_service.time.sleep") as slept:
        _post_with_retry(client, "https://example.test/x")

    assert slept.call_args[0][0] <= 30.0


def test_it_gives_up_rather_than_retrying_forever():
    """Bounded: a stuck rate limit must not hold the batch open indefinitely."""
    client = _client(*[_response(429, body={"retry_after": 0.1})] * (_MAX_RATE_LIMIT_RETRIES + 5))

    with patch("apps.accounts.discord_service.time.sleep"):
        result = _post_with_retry(client, "https://example.test/x")

    assert result.status_code == 429
    assert client.post.call_count == _MAX_RATE_LIMIT_RETRIES + 1


@pytest.mark.parametrize("status", [403, 404, 500])
def test_other_errors_are_returned_immediately_for_the_caller_to_raise(status):
    """Retrying a closed DM or a missing user just slows the batch down."""
    client = _client(_response(status, body={}))

    with patch("apps.accounts.discord_service.time.sleep") as slept:
        result = _post_with_retry(client, "https://example.test/x")

    assert result.status_code == status
    assert client.post.call_count == 1
    slept.assert_not_called()


def test_a_first_try_success_never_sleeps():
    client = _client(_response(200, body={"id": "1"}))

    with patch("apps.accounts.discord_service.time.sleep") as slept:
        _post_with_retry(client, "https://example.test/x")

    slept.assert_not_called()
