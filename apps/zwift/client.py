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
- ``GET  /api/zwift/users/<id>/profile-stats`` -> windowed metric min/max
- ``POST /api/zwift/users/<id>/profile/refresh`` -> the profile, re-read from Zwift now
"""

from __future__ import annotations

import httpx
import logfire

from gotta_bike_platform.config import settings as config

_TIMEOUT = 15.0

# The refresh reads Zwift inside the service's request, so it can take longer than the stored-copy
# reads above. It only runs from a background task, so nobody is waiting on the page.
_REFRESH_TIMEOUT = 30.0


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


def get_racing_profile(user_id: str) -> dict | None:
    """Fetch a connected user's Zwift racing profile from the service.

    Args:
        user_id: The platform user identifier (stable primary key).

    Returns:
        The racing-profile dict (denormalized metrics + full ``data`` DTO), or
        None if the service is unconfigured, the user isn't connected (404), or
        the call failed. A live upstream fetch may be triggered service-side when
        no snapshot is stored yet.

    """
    if not is_configured():
        return None
    try:
        response = httpx.get(
            _url(f"/api/zwift/users/{user_id}/profile"),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logfire.error("Zwift racing profile fetch failed", user_id=user_id, error=str(e))
        return None


def refresh_racing_profile(user_id: str) -> dict | None:
    """Have the service re-read a connected user's racing profile from Zwift, now.

    Unlike ``get_racing_profile``, which serves the service's stored copy, this makes the
    service call Zwift during the request and store the result, so the weight and height a
    rider has just changed in Zwift are in the response. The service throttles it: a profile
    fetched in the last 60 seconds comes back as stored, with ``refreshed: false``.

    On any error the service leaves its stored profile as it was, so a failed refresh loses
    nothing; the next scheduled read picks the change up instead.

    Args:
        user_id: The platform user identifier (stable primary key).

    Returns:
        The racing-profile dict (as ``get_racing_profile``) plus ``refreshed``, or None if the
        service is unconfigured, the user isn't connected, or the call failed. A 404 means
        either that the user isn't connected or that Zwift has no profile for them; the
        service passes Zwift's 404 through unchanged.

    """
    if not is_configured():
        return None
    try:
        response = httpx.post(
            _url(f"/api/zwift/users/{user_id}/profile/refresh"),
            headers=_headers(),
            timeout=_REFRESH_TIMEOUT,
        )
    except httpx.HTTPError as e:
        logfire.error("Zwift profile refresh failed", user_id=user_id, error=type(e).__name__)
        return None

    if response.status_code == 404:
        logfire.info("Zwift profile refresh found no profile: not connected, or none on Zwift", user_id=user_id)
        return None
    if response.status_code == 429:
        logfire.warning("Zwift profile refresh rate limited by Zwift", user_id=user_id)
        return None
    if not response.is_success:
        logfire.error("Zwift profile refresh failed", user_id=user_id, status_code=response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        logfire.error("Zwift profile refresh returned a body that is not JSON", user_id=user_id)
        return None


def get_profile_stats(user_id: str) -> dict | None:
    """Fetch windowed min/max of a connected user's racing metrics.

    Summarizes the service's deduped racing-profile snapshot history. Note the
    windowed weight is the *competition-metrics* weight (the value Zwift raced the
    rider at), not the live profile weight, and a metric only appears once a
    snapshot inside the window carries it.

    Args:
        user_id: The platform user identifier (stable primary key).

    Returns:
        ``{"zwid", "current": {...}, "windows": {"30d"/"60d"/"90d": {field:
        {"min", "max", "first", "last", "count"} | None}}}``, or None if the
        service is unconfigured, the user isn't connected (404), or the call failed.

    """
    if not is_configured():
        return None
    try:
        response = httpx.get(
            _url(f"/api/zwift/users/{user_id}/profile-stats"),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logfire.error("Zwift profile stats fetch failed", user_id=user_id, error=str(e))
        return None


def get_activity_stats(user_id: str, days: int = 30) -> dict | None:
    """Fetch a connected user's recent activities + aggregate stats.

    Args:
        user_id: The platform user identifier (stable primary key).
        days: Rolling window in days (service clamps to 1-90).

    Returns:
        A dict ``{"stats": {...}, "activities": [...]}`` for the window, or None
        if the service is unconfigured, the user isn't connected (404), or the
        call failed.

    """
    if not is_configured():
        return None
    try:
        response = httpx.get(
            _url(f"/api/zwift/users/{user_id}/activity-stats"),
            params={"days": days},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logfire.error("Zwift activity stats fetch failed", user_id=user_id, error=str(e))
        return None


def list_connections() -> list[dict] | None:
    """Fetch all of this app's connected users from the service (admin view).

    Returns:
        A list of ``{"user_id", "zwid", "connected_at", "updated_at",
        "zwift_name", "category", "category_women"}`` dicts (newest first), or
        None if the service is unconfigured or the call failed.

    """
    if not is_configured():
        return None
    try:
        response = httpx.get(
            _url("/api/zwift/oauth/connections"),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logfire.error("Zwift connections fetch failed", error=str(e))
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
