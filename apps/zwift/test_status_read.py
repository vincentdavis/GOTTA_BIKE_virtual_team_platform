"""``client.get_connection_status``: the read every page render makes.

A hung zauth service used to hold each render for the full 15 s client timeout, and ask again
on the next load. Status reads now default to a short timeout and a failed read is remembered
for a minute. ``httpx.get`` is patched at the client module, so nothing leaves the process.
"""

from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache

from apps.zwift import client
from gotta_bike_platform.config import settings as config

BASE = "http://svc.internal:8000"
URL = f"{BASE}/api/zwift/oauth/status"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Configure the real client, and start without a failure remembered by another test."""
    monkeypatch.setattr(config, "zwift_api_base_url", BASE)
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key")
    cache.clear()
    yield
    cache.clear()


def _response(status_code: int, body=None, text=None) -> httpx.Response:
    """Build a response the way httpx would hand it back.

    Returns:
        The response.

    """
    request = httpx.Request("GET", URL)
    if text is not None:
        return httpx.Response(status_code, text=text, request=request)
    return httpx.Response(status_code, json=body, request=request)


CONNECTED = {"connected": True, "zwid": "4242", "connected_at": None}


def test_a_status_read_uses_the_short_timeout_by_default():
    with patch("apps.zwift.client.httpx.get", return_value=_response(200, CONNECTED)) as get:
        assert client.get_connection_status("7") == CONNECTED

    timeout = get.call_args.kwargs["timeout"]
    assert timeout is client.STATUS_TIMEOUT
    assert timeout.connect <= 1.0
    assert timeout.read <= 3.0


def test_a_caller_that_can_wait_can_ask_for_longer():
    with patch("apps.zwift.client.httpx.get", return_value=_response(200, CONNECTED)) as get:
        client.get_connection_status("7", timeout=15)

    assert get.call_args.kwargs["timeout"] == 15


@pytest.mark.parametrize(
    "failure",
    [
        {"side_effect": httpx.ReadTimeout("slow")},
        {"side_effect": httpx.ConnectError("refused")},
        {"return_value": _response(503, {"detail": "down"})},
        {"return_value": _response(200, text="<html>not json</html>")},
        {"return_value": _response(200, ["not", "a", "dict"])},
    ],
    ids=["timeout", "refused", "5xx", "not-json", "not-a-dict"],
)
def test_a_failed_read_is_remembered_so_the_next_page_does_not_wait(failure):
    with patch("apps.zwift.client.httpx.get", **failure) as get:
        assert client.get_connection_status("7") is None
        assert client.get_connection_status("7") is None

    assert get.call_count == 1


def test_the_memory_is_per_id():
    with patch("apps.zwift.client.httpx.get", side_effect=httpx.ReadTimeout("slow")):
        client.get_connection_status("7")
    with patch("apps.zwift.client.httpx.get", return_value=_response(200, CONNECTED)) as get:
        assert client.get_connection_status("8") == CONNECTED

    get.assert_called_once()


def test_the_service_is_asked_again_once_the_memory_expires():
    with patch("apps.zwift.client.httpx.get", side_effect=httpx.ReadTimeout("slow")):
        client.get_connection_status("7")
    cache.delete(client._status_unavailable_key("7"))

    with patch("apps.zwift.client.httpx.get", return_value=_response(200, CONNECTED)) as get:
        assert client.get_connection_status("7") == CONNECTED

    get.assert_called_once()


def test_a_real_answer_is_never_remembered():
    """A rider can finish consent in another tab; the next read has to see it."""
    answers = [_response(200, {"connected": False}), _response(200, CONNECTED)]
    with patch("apps.zwift.client.httpx.get", side_effect=answers) as get:
        assert client.get_connection_status("7") == {"connected": False}
        assert client.get_connection_status("7") == CONNECTED

    assert get.call_count == 2


def test_nothing_is_remembered_when_the_service_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "zwift_app_api_key", None)
    with patch("apps.zwift.client.httpx.get") as get:
        assert client.get_connection_status("7") is None
    get.assert_not_called()

    monkeypatch.setattr(config, "zwift_app_api_key", "app-key")
    with patch("apps.zwift.client.httpx.get", return_value=_response(200, CONNECTED)) as get:
        assert client.get_connection_status("7") == CONNECTED


def test_a_refused_key_logs_the_status_code_and_nothing_scrubbable():
    """``str(e)`` of a 401 says "Unauthorized", which production Logfire scrubs whole."""
    with (
        patch("apps.zwift.client.httpx.get", return_value=_response(401, {"detail": "bad key"})),
        patch("apps.zwift.client.logfire") as fake_logfire,
    ):
        assert client.get_connection_status("7") is None

    kwargs = fake_logfire.error.call_args.kwargs
    assert kwargs == {"user_id": "7", "status_code": 401}


def test_a_network_error_logs_its_type_not_its_message():
    """The message is left out: httpx quotes the request URL in it."""
    with (
        patch("apps.zwift.client.httpx.get", side_effect=httpx.ConnectError(f"cannot reach {URL}?user_id=7")),
        patch("apps.zwift.client.logfire") as fake_logfire,
    ):
        client.get_connection_status("7")

    kwargs = fake_logfire.error.call_args.kwargs
    assert kwargs == {"user_id": "7", "error": "ConnectError"}
    assert URL not in str(fake_logfire.mock_calls)


# --- a fresh answer where it matters ----------------------------------------------------


def test_forgetting_a_failure_lets_the_next_read_ask_again():
    with patch("apps.zwift.client.httpx.get", side_effect=httpx.ReadTimeout("slow")):
        assert client.get_connection_status("7") is None

    client.forget_status_failure("7")
    with patch("apps.zwift.client.httpx.get", return_value=_response(200, CONNECTED)) as get:
        assert client.get_connection_status("7") == CONNECTED
    get.assert_called_once()


@pytest.mark.django_db
def test_coming_back_from_consent_reads_afresh_after_a_recent_failure(client_, team_member):
    """The rider left for Zwift just after a failed read; their new connection must still show."""
    with patch("apps.zwift.client.httpx.get", side_effect=httpx.ReadTimeout("slow")):
        client_.force_login(team_member)
        client_.get("/user/zauth/")

    status = {"connected": True, "zwid": "4242", "connected_at": None}
    with (
        patch("apps.zwift.client.httpx.get", return_value=_response(200, status)) as get,
        patch("apps.zwift.client.get_racing_profile", return_value=None),
    ):
        response = client_.get("/user/zauth/?status=connected")

    get.assert_called_once()
    team_member.refresh_from_db()
    assert team_member.zwid_verified is True
    assert team_member.zwid == 4242
    assert "couldn" not in response.content.decode().lower()


@pytest.mark.django_db
def test_a_plain_visit_still_honours_a_recent_failure(client_, team_member):
    """Only the consent return skips the memory; ordinary loads keep sparing a hung service."""
    client_.force_login(team_member)
    with patch("apps.zwift.client.httpx.get", side_effect=httpx.ReadTimeout("slow")):
        client_.get("/user/zauth/")
    with patch("apps.zwift.client.httpx.get") as get:
        client_.get("/user/zauth/")
    get.assert_not_called()


@pytest.mark.django_db
def test_a_registration_coming_back_from_consent_reads_afresh(client_):
    from apps.team.models import MembershipApplication

    application = MembershipApplication.objects.create(discord_id="990000000000000009", discord_username="applicant")
    page = f"/team/apply/{application.pk}/"
    with patch("apps.zwift.client.httpx.get", side_effect=httpx.ReadTimeout("slow")):
        client_.get(page)

    status = {"connected": True, "zwid": "4242", "connected_at": None}
    with patch("apps.zwift.client.httpx.get", return_value=_response(200, status)) as get:
        client_.get(f"{page}?status=connected&zwid=4242")

    get.assert_called_once()
    application.refresh_from_db()
    assert application.zwift_verified is True
    assert application.zwift_id == "4242"


@pytest.fixture
def client_(client):
    """Name the Django test client apart from the zwift client module imported above.

    Returns:
        The test client.

    """
    return client
