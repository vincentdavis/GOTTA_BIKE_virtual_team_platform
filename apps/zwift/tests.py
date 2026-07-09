"""Tests for the zwift app's /user/zauth connection page.

The service HTTP calls are patched at the ``apps.zwift.client`` boundary so no
real network traffic happens.
"""

import pytest
from django.urls import reverse


@pytest.fixture
def logged_in_client(client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_zauth_redirects_anonymous_to_login(client):
    resp = client.get(reverse("zwift:zauth"))
    assert resp.status_code == 302
    assert "/accounts/login" in resp["Location"] or "login" in resp["Location"]


@pytest.mark.django_db
def test_zauth_shows_not_configured(logged_in_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)
    resp = logged_in_client.get(reverse("zwift:zauth"))
    assert resp.status_code == 200
    assert b"isn&#x27;t configured" in resp.content or b"configured yet" in resp.content


@pytest.mark.django_db
def test_zauth_shows_connected(logged_in_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.get_connection_status",
        lambda user_id: {"connected": True, "zwid": "12345", "connected_at": "2026-07-01T10:00:00Z"},
    )
    resp = logged_in_client.get(reverse("zwift:zauth"))
    assert resp.status_code == 200
    assert b"Connected" in resp.content
    assert b"12345" in resp.content
    assert b"Disconnect Zwift" in resp.content


@pytest.mark.django_db
def test_zauth_shows_not_connected_with_connect_button(logged_in_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.get_connection_status",
        lambda user_id: {"connected": False, "zwid": None, "connected_at": None},
    )
    resp = logged_in_client.get(reverse("zwift:zauth"))
    assert resp.status_code == 200
    assert b"Not connected" in resp.content
    assert b"Connect to Zwift" in resp.content


@pytest.mark.django_db
def test_zauth_shows_service_error(logged_in_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda user_id: None)
    resp = logged_in_client.get(reverse("zwift:zauth"))
    assert resp.status_code == 200
    assert b"couldn&#x27;t reach the Zwift service" in resp.content.lower() or b"try again" in resp.content


@pytest.mark.django_db
def test_connect_redirects_to_authorize_url(logged_in_client, user, monkeypatch):
    captured = {}

    def _fake_authorize(user_id, return_url, *, prompt_login=False):
        captured["user_id"] = user_id
        captured["return_url"] = return_url
        return "https://secure.zwift.com/auth/realms/zwift/authorize?state=abc"

    monkeypatch.setattr("apps.zwift.client.get_authorize_url", _fake_authorize)
    resp = logged_in_client.post(reverse("zwift:zauth_connect"))

    assert resp.status_code == 302
    assert resp["Location"].startswith("https://secure.zwift.com/")
    assert captured["user_id"] == str(user.pk)
    assert captured["return_url"].endswith(reverse("zwift:zauth"))


@pytest.mark.django_db
def test_connect_handles_failure_gracefully(logged_in_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_authorize_url", lambda *a, **k: None)
    resp = logged_in_client.post(reverse("zwift:zauth_connect"))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("zwift:zauth")


@pytest.mark.django_db
def test_connect_requires_post(logged_in_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.get_authorize_url", lambda *a, **k: "https://x")
    resp = logged_in_client.get(reverse("zwift:zauth_connect"))
    assert resp.status_code == 405


@pytest.mark.django_db
def test_disconnect_calls_service_and_redirects(logged_in_client, user, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "apps.zwift.client.disconnect",
        lambda user_id: captured.setdefault("user_id", user_id) or True,
    )
    resp = logged_in_client.post(reverse("zwift:zauth_disconnect"))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("zwift:zauth")
    assert captured["user_id"] == str(user.pk)


@pytest.mark.django_db
def test_client_get_authorize_url_sends_key_and_payload(monkeypatch):
    """The client posts the app key + payload and returns the authorize_url."""
    from apps.zwift import client
    from gotta_bike_platform.config import settings as config

    monkeypatch.setattr(config, "zwift_api_base_url", "http://svc.internal:8000")
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key-123")

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"authorize_url": "https://secure.zwift.com/x"}

    def _fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr("apps.zwift.client.httpx.post", _fake_post)

    result = client.get_authorize_url("42", "https://app.example.com/user/zauth/")

    assert result == "https://secure.zwift.com/x"
    assert captured["url"] == "http://svc.internal:8000/api/zwift/oauth/authorize-url"
    assert captured["headers"]["X-API-Key"] == "app-key-123"
    assert captured["json"]["user_id"] == "42"


@pytest.mark.django_db
def test_client_returns_none_when_unconfigured(monkeypatch):
    from apps.zwift import client
    from gotta_bike_platform.config import settings as config

    monkeypatch.setattr(config, "zwift_api_base_url", None)
    monkeypatch.setattr(config, "zwift_app_api_key", None)

    assert client.is_configured() is False
    assert client.get_connection_status("42") is None
    assert client.get_authorize_url("42", "https://x") is None
    assert client.disconnect("42") is False
