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
def test_zauth_view_stamps_verification_on_connect(logged_in_client, user, monkeypatch):
    """Visiting the zauth page while connected reconciles platform verification."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.get_connection_status",
        lambda user_id: {"connected": True, "zwid": "12345", "zwift_user_id": "uuid-1", "connected_at": None},
    )

    logged_in_client.get(reverse("zwift:zauth"))

    user.refresh_from_db()
    assert user.zwid == 12345
    assert user.zwid_verified is True
    assert user.zwid_verification_method == "zauth"


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
    assert b"Connect with Zwift" in resp.content


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
    assert client.list_connections() is None
    assert client.get_racing_profile("42") is None
    assert client.get_activity_stats("42") is None
    assert client.disconnect("42") is False


@pytest.mark.django_db
def test_client_get_racing_profile_returns_data(monkeypatch):
    from apps.zwift import client
    from gotta_bike_platform.config import settings as config

    monkeypatch.setattr(config, "zwift_api_base_url", "http://svc.internal:8000")
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key-123")

    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"zwid": "555", "category": "B", "racing_score": 435.0}

    def _fake_get(url, *, headers, timeout):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr("apps.zwift.client.httpx.get", _fake_get)

    result = client.get_racing_profile("62")

    assert result["category"] == "B"
    assert captured["url"] == "http://svc.internal:8000/api/zwift/users/62/profile"


@pytest.mark.django_db
def test_client_get_racing_profile_404_returns_none(monkeypatch):
    from apps.zwift import client
    from gotta_bike_platform.config import settings as config

    monkeypatch.setattr(config, "zwift_api_base_url", "http://svc.internal:8000")
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key-123")

    class _Resp:
        status_code = 404

        def raise_for_status(self):  # pragma: no cover - not reached on 404
            raise AssertionError("should short-circuit on 404")

        def json(self):  # pragma: no cover
            return {}

    monkeypatch.setattr("apps.zwift.client.httpx.get", lambda url, *, headers, timeout: _Resp())

    assert client.get_racing_profile("999") is None


# --- admin connections page -------------------------------------------------


@pytest.fixture
def membership_admin_client(client, user_model):
    admin = user_model.objects.create_user(
        username="mem_admin", email="mem_admin@example.test", permission_overrides={"membership_admin": True}
    )
    client.force_login(admin)
    return client


@pytest.mark.django_db
def test_zwift_connections_requires_membership_admin(auth_client):
    # A plain team_member (no membership_admin) is forbidden.
    resp = auth_client.get(reverse("team:zwift_connections"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_zwift_connections_lists_and_joins_users(membership_admin_client, user_model, monkeypatch):
    # The page reports on Discord-linked members, so the joined user needs a discord_id;
    # a connection whose id matches no member is surfaced in the orphans table instead.
    linked = user_model.objects.create_user(username="linked", email="linked@example.test", discord_id="42")
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [
            {
                "user_id": str(linked.pk),
                "zwid": "12345",
                "connected_at": "2026-07-01T10:00:00Z",
                "zwift_name": "Linked Rider",
                "category": "B",
                "category_women": None,
            },
            {"user_id": "999999", "zwid": "888", "connected_at": None, "zwift_name": None, "category": None},
        ],
    )

    resp = membership_admin_client.get(reverse("team:zwift_connections"))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "12345" in body
    assert "Linked Rider" in body
    assert "1 connected now" in body  # only the id matching a member counts
    assert "999999" in body  # the unmatched id is still surfaced


@pytest.mark.django_db
def test_zwift_connections_service_error(membership_admin_client, monkeypatch):
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: None)

    resp = membership_admin_client.get(reverse("team:zwift_connections"))

    assert resp.status_code == 200
    assert b"reach the Zwift service" in resp.content
