"""The zauth disconnect call, with "no link" kept apart from "the call failed".

``disconnect()`` answers False for both, which is fine for a rider pressing "Remove" but not
for an erasure: only a failed call leaves a link behind. ``httpx.post`` is patched at the
client module, so nothing leaves the process.
"""

from unittest.mock import patch

import httpx
import pytest

from apps.zwift import client
from apps.zwift.client import DisconnectOutcome
from gotta_bike_platform.config import settings as config

URL = "http://svc.internal:8000/api/zwift/oauth/disconnect"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "zwift_api_base_url", "http://svc.internal:8000")
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key-123")


def _response(status_code: int, body=None, text=None) -> httpx.Response:
    """Build a response the way httpx would hand it back.

    Returns:
        The response.

    """
    request = httpx.Request("POST", URL)
    if text is not None:
        return httpx.Response(status_code, text=text, request=request)
    if body is None:
        return httpx.Response(status_code, request=request)
    return httpx.Response(status_code, json=body, request=request)


def test_the_request_carries_the_app_key_and_the_id(configured):
    with patch("apps.zwift.client.httpx.post", return_value=_response(200, {"disconnected": True})) as post:
        client.disconnect_link("17")

    args, kwargs = post.call_args
    assert args[0] == URL
    assert kwargs["headers"] == {"X-API-Key": "app-key-123"}
    assert kwargs["json"] == {"user_id": "17"}


@pytest.mark.parametrize(
    ("response", "expected", "as_bool"),
    [
        (_response(200, {"disconnected": True}), DisconnectOutcome.REMOVED, True),
        (_response(200, {"disconnected": False}), DisconnectOutcome.NO_LINK, False),
        (_response(500), DisconnectOutcome.FAILED, False),
        (_response(401, {"detail": "bad key"}), DisconnectOutcome.FAILED, False),
        (_response(200, {"something": "else"}), DisconnectOutcome.FAILED, False),
        (_response(200, ["not", "a", "dict"]), DisconnectOutcome.FAILED, False),
        (_response(200, text="<html>"), DisconnectOutcome.FAILED, False),
    ],
)
def test_every_answer_becomes_an_outcome(configured, response, expected, as_bool):
    with patch("apps.zwift.client.httpx.post", return_value=response):
        assert client.disconnect_link("17") is expected
    with patch("apps.zwift.client.httpx.post", return_value=response):
        assert client.disconnect("17") is as_bool


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_a_network_failure_is_a_failure_not_an_exception(configured, error):
    with patch("apps.zwift.client.httpx.post", side_effect=error):
        assert client.disconnect_link("17") is DisconnectOutcome.FAILED


def test_nothing_is_asked_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "zwift_api_base_url", None)
    monkeypatch.setattr(config, "zwift_app_api_key", None)

    with patch("apps.zwift.client.httpx.post") as post:
        assert client.disconnect_link("17") is DisconnectOutcome.UNCONFIGURED
    post.assert_not_called()


def test_the_failure_log_carries_no_url(configured):
    """The exception message quotes the request URL, so the log takes the type instead."""
    with (
        patch("apps.zwift.client.httpx.post", side_effect=httpx.ConnectError(f"cannot reach {URL}")),
        patch("apps.zwift.client.logfire") as fake_logfire,
    ):
        client.disconnect_link("17")

    kwargs = fake_logfire.error.call_args.kwargs
    assert kwargs == {"user_id": "17", "error": "ConnectError"}
