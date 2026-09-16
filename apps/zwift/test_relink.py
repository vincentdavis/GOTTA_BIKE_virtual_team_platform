"""The zauth relink call that moves a registration's Zwift link onto a member.

``httpx.post`` is patched at the client module, so nothing leaves the process. Every
response is turned into an outcome rather than an exception: the import that calls this
must not break because the service is down or older than the endpoint.
"""

from unittest.mock import patch

import httpx
import pytest

from apps.zwift import client
from apps.zwift.client import RelinkOutcome, RelinkResult
from gotta_bike_platform.config import settings as config

APPLICATION_ID = "5f0c1f0e-7d36-4a55-9d0e-1f2a3b4c5d6e"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "zwift_api_base_url", "http://svc.internal:8000")
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key-123")


def _response(status_code: int, body=None) -> httpx.Response:
    """Build a response the way httpx would hand it back.

    Returns:
        The response, with a JSON body when one is given.

    """
    request = httpx.Request("POST", "http://svc.internal:8000/api/zwift/oauth/relink")
    if body is None:
        return httpx.Response(status_code, request=request)
    return httpx.Response(status_code, json=body, request=request)


def test_the_request_carries_the_app_key_and_both_ids(configured):
    with patch("apps.zwift.client.httpx.post", return_value=_response(200, {"relinked": True, "zwid": "4242"})) as post:
        client.relink_connection(APPLICATION_ID, "17")

    args, kwargs = post.call_args
    assert args[0] == "http://svc.internal:8000/api/zwift/oauth/relink"
    assert kwargs["headers"] == {"X-API-Key": "app-key-123"}
    assert kwargs["json"] == {"from_user_id": APPLICATION_ID, "to_user_id": "17"}


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (200, {"relinked": True, "zwid": "4242", "connected_at": "2026-09-01T10:00Z"}, RelinkResult("moved", "4242")),
        # An integer zwid is the same rider; a missing one is still a move.
        (200, {"relinked": True, "zwid": 4242, "connected_at": None}, RelinkResult("moved", "4242")),
        (200, {"relinked": True, "zwid": None, "connected_at": None}, RelinkResult("moved", None)),
        (404, {"detail": "no link"}, RelinkResult("not_found")),
        # What a zauth deploy without the endpoint answers: the same outcome, not an error.
        (404, None, RelinkResult("not_found")),
        (409, {"detail": "linked elsewhere"}, RelinkResult("conflict")),
        (400, {"detail": "ids equal"}, RelinkResult("error")),
        (401, {"detail": "bad key"}, RelinkResult("error")),
        (503, None, RelinkResult("error")),
        # A 200 that does not say it relinked is not trusted as a move.
        (200, {"relinked": False}, RelinkResult("error")),
        (200, ["not", "a", "dict"], RelinkResult("error")),
    ],
)
def test_every_answer_becomes_an_outcome(configured, status_code, body, expected):
    with patch("apps.zwift.client.httpx.post", return_value=_response(status_code, body)):
        assert client.relink_connection(APPLICATION_ID, "17") == expected


def test_a_body_that_is_not_json_is_an_error(configured):
    request = httpx.Request("POST", "http://svc.internal:8000/api/zwift/oauth/relink")
    with patch("apps.zwift.client.httpx.post", return_value=httpx.Response(200, text="<html>", request=request)):
        assert client.relink_connection(APPLICATION_ID, "17").outcome == RelinkOutcome.ERROR


def test_a_network_failure_is_an_error_not_an_exception(configured):
    with patch("apps.zwift.client.httpx.post", side_effect=httpx.ConnectError("refused")):
        assert client.relink_connection(APPLICATION_ID, "17") == RelinkResult(RelinkOutcome.ERROR)


def test_nothing_is_asked_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "zwift_api_base_url", None)
    monkeypatch.setattr(config, "zwift_app_api_key", None)

    with patch("apps.zwift.client.httpx.post") as post:
        result = client.relink_connection(APPLICATION_ID, "17")

    assert result == RelinkResult(RelinkOutcome.UNCONFIGURED)
    post.assert_not_called()


@pytest.mark.parametrize("status_code", [200, 404, 409, 401])
def test_the_logs_carry_ids_and_the_status_only(configured, status_code):
    """Nothing from the body -- and no attribute Logfire's scrubber would blank for its name."""
    body = {"relinked": True, "zwid": "4242", "zwift_name": "Somebody Real"}
    with (
        patch("apps.zwift.client.httpx.post", return_value=_response(status_code, body)),
        patch("apps.zwift.client.logfire") as fake_logfire,
    ):
        client.relink_connection(APPLICATION_ID, "17")

    (call,) = [c for c in fake_logfire.mock_calls if c[0] in {"info", "warning", "error"}]
    assert call.kwargs == {"from_user_id": APPLICATION_ID, "to_user_id": "17", "status_code": status_code}


def test_a_network_failure_log_does_not_quote_the_url(configured):
    """The exception type is logged, not the message: httpx quotes the request URL in it."""
    with (
        patch("apps.zwift.client.httpx.post", side_effect=httpx.ConnectError("http://svc.internal:8000/x failed")),
        patch("apps.zwift.client.logfire") as fake_logfire,
    ):
        client.relink_connection(APPLICATION_ID, "17")

    kwargs = fake_logfire.error.call_args.kwargs
    assert kwargs["error"] == "ConnectError"
    assert "svc.internal" not in str(fake_logfire.mock_calls)
