"""HTTP client for the GOTTA_BIKE Zwift API service.

The official Zwift OAuth tokens live entirely in that private microservice; the
platform only ever learns whether a user is connected and their zwid. All calls
here are server-to-server over the internal Railway network, authenticated with
the platform's per-app key (``config.zwift_app_api_key``) sent as ``X-API-Key``.

The service base URL and key come from ``gotta_bike_platform/config.py``
(``ZWIFT_API_BASE_URL`` / ``ZWIFT_APP_API_KEY`` env vars), not constance.

See the service's endpoints:
- ``POST /api/zwift/oauth/authorize-url`` -> ``{authorize_url}``
- ``GET  /api/zwift/oauth/status?user_id=`` -> ``{connected, zwid, connected_at}``
- ``POST /api/zwift/oauth/disconnect`` -> ``{disconnected}``
"""

from __future__ import annotations

import httpx
import logfire

from gotta_bike_platform.config import settings as config

_TIMEOUT = 15.0


def is_configured() -> bool:
    """Report whether the Zwift API service connection is configured.

    Returns:
        True when both the service base URL and the per-app key are set.

    """
    return bool(config.zwift_api_base_url and config.zwift_app_api_key)


def _url(path: str) -> str:
    """Build a full service URL from a path.

    Args:
        path: The endpoint path (e.g. ``/api/zwift/oauth/status``).

    Returns:
        The absolute URL against the configured service base.

    """
    return f"{(config.zwift_api_base_url or '').rstrip('/')}{path}"


def _headers() -> dict[str, str]:
    """Build the request headers, including the per-app API key.

    Returns:
        Headers dict with the ``X-API-Key`` shared secret.

    """
    return {"X-API-Key": config.zwift_app_api_key or ""}


def get_connection_status(user_id: str) -> dict | None:
    """Fetch a user's Zwift connection status from the service.

    Args:
        user_id: The platform user identifier (stable primary key) to look up.

    Returns:
        A dict ``{"connected": bool, "zwid": str | None, "connected_at": str | None}``,
        or None if the service is unconfigured or the call failed.

    """
    if not is_configured():
        return None
    try:
        response = httpx.get(
            _url("/api/zwift/oauth/status"),
            params={"user_id": user_id},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logfire.error("Zwift status fetch failed", user_id=user_id, error=str(e))
        return None


def get_authorize_url(user_id: str, return_url: str, *, prompt_login: bool = False) -> str | None:
    """Request a Zwift consent URL to start connecting the user's account.

    Args:
        user_id: The platform user identifier (stable primary key).
        return_url: Where the service should redirect the browser after consent
            (must be allow-listed in the service's ``ALLOWED_RETURN_ORIGINS``).
        prompt_login: Force Zwift re-authentication (for account switching).

    Returns:
        The Zwift authorization URL to redirect the browser to, or None on error.

    """
    if not is_configured():
        return None
    try:
        response = httpx.post(
            _url("/api/zwift/oauth/authorize-url"),
            json={"user_id": user_id, "return_url": return_url, "prompt_login": prompt_login},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("authorize_url")
    except httpx.HTTPError as e:
        logfire.error("Zwift authorize-url request failed", user_id=user_id, error=str(e))
        return None


def disconnect(user_id: str) -> bool:
    """Disconnect a user's Zwift account link in the service.

    Args:
        user_id: The platform user identifier (stable primary key).

    Returns:
        True if a link existed and was removed, False otherwise (or on error).

    """
    if not is_configured():
        return False
    try:
        response = httpx.post(
            _url("/api/zwift/oauth/disconnect"),
            json={"user_id": user_id},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return bool(response.json().get("disconnected"))
    except httpx.HTTPError as e:
        logfire.error("Zwift disconnect failed", user_id=user_id, error=str(e))
        return False
