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
- ``POST /api/zwift/oauth/relink`` -> ``{relinked, zwid, connected_at}`` (moves a link between ids)
- ``GET  /api/zwift/users/<id>/profile-stats`` -> windowed metric min/max
- ``POST /api/zwift/users/<id>/profile/refresh`` -> the profile, re-read from Zwift now
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import httpx
import logfire
from django.core.cache import cache

from gotta_bike_platform.config import settings as config

_TIMEOUT = 15.0

# The refresh reads Zwift inside the service's request, so it can take longer than the stored-copy
# reads above. It only runs from a background task, so nobody is waiting on the page.
_REFRESH_TIMEOUT = 30.0

# Every status read happens while somebody waits for a page: the profile and its edit page, the
# import page and its POST, the registration page, a verification record. The web tier has only a
# handful of threads (WEB_WORKERS x WEB_BLOCKING_THREADS), so a hung service must cost a rider a few
# seconds rather than hold a thread for the full _TIMEOUT.
STATUS_TIMEOUT = httpx.Timeout(3.0, connect=1.0)

# After a failed status read the same id is not asked again for this long, so a hung service is
# not re-asked on every page load. Only failures are remembered: a real answer can change at any
# moment (a rider finishing consent in another tab), and callers rely on seeing that at once.
STATUS_UNAVAILABLE_SECONDS = 60


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


def _status_unavailable_key(user_id: str) -> str:
    """Build the cache key that marks a recent failed status read for an id.

    Args:
        user_id: The platform user identifier (a user's primary key, or a registration's UUID).

    Returns:
        The cache key.

    """
    return f"zwift-status-unavailable:v1:{user_id}"


def forget_status_failure(user_id: str) -> None:
    """Let the next status read for an id ask the service, even after a recent failure.

    For the moments a fresh answer matters more than sparing a slow service: a rider coming
    back from Zwift's consent page, or a link the service has just moved. Without this, a read
    that failed a few seconds earlier would hide the new connection for up to
    :data:`STATUS_UNAVAILABLE_SECONDS`.

    Args:
        user_id: The platform user identifier (a user's primary key, or a registration's UUID).

    """
    cache.delete(_status_unavailable_key(user_id))


def get_connection_status(user_id: str, *, timeout: float | httpx.Timeout = STATUS_TIMEOUT) -> dict | None:
    """Fetch a user's Zwift connection status from the service.

    The default timeout is the short :data:`STATUS_TIMEOUT`, because every current caller is
    rendering a page. A background caller that can afford to wait passes a longer one.

    A failed read is remembered for :data:`STATUS_UNAVAILABLE_SECONDS`; until then the id is
    answered with None without asking. None already means "unknown" to every caller (nothing
    revokes on it), so this only saves the wait.

    Args:
        user_id: The platform user identifier (stable primary key) to look up.
        timeout: The httpx timeout for this read.

    Returns:
        A dict ``{"connected": bool, "zwid": str | None, "connected_at": str | None}``,
        or None if the service is unconfigured or the call failed (now or moments ago).

    """
    if not is_configured():
        return None
    unavailable_key = _status_unavailable_key(user_id)
    if cache.get(unavailable_key):
        logfire.debug("Zwift status read skipped: it failed moments ago", user_id=user_id)
        return None
    try:
        response = httpx.get(
            _url("/api/zwift/oauth/status"),
            params={"user_id": user_id},
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as e:
        # The status code, not str(e): httpx quotes the URL in its message, and a 401's
        # "Unauthorized" would have the whole value scrubbed in production.
        logfire.error("Zwift status fetch failed", user_id=user_id, status_code=e.response.status_code)
        body = None
    except httpx.HTTPError as e:
        logfire.error("Zwift status fetch failed", user_id=user_id, error=type(e).__name__)
        body = None
    except ValueError:
        # A 200 that is not JSON used to escape as an exception and fail the whole page.
        logfire.error("Zwift status fetch returned a body that is not JSON", user_id=user_id)
        body = None

    if isinstance(body, dict):
        return body
    if body is not None:
        logfire.error("Zwift status fetch returned an unexpected body", user_id=user_id)
    cache.set(unavailable_key, True, STATUS_UNAVAILABLE_SECONDS)
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


class DisconnectOutcome(StrEnum):
    """What a :func:`disconnect_link` call did."""

    # The service had a link for this id and removed it.
    REMOVED = "removed"
    # The service answered that there was no link to remove.
    NO_LINK = "no_link"
    # The call failed (service down, refused, an unexpected answer): whether a link is
    # still there is unknown.
    FAILED = "failed"
    # The service connection is not configured, so nothing was asked.
    UNCONFIGURED = "unconfigured"


def disconnect_link(user_id: str) -> DisconnectOutcome:
    """Disconnect a user's Zwift account link in the service, saying what happened.

    Unlike :func:`disconnect`, this keeps "there was no link" apart from "the call
    failed". An erasure needs that: only the second leaves a link behind.

    Never raises.

    Args:
        user_id: The platform user identifier (a user's primary key, or a registration's UUID).

    Returns:
        A :class:`DisconnectOutcome`.

    """
    if not is_configured():
        return DisconnectOutcome.UNCONFIGURED
    try:
        response = httpx.post(
            _url("/api/zwift/oauth/disconnect"),
            json={"user_id": user_id},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        # type(e) rather than str(e): httpx quotes the request URL in its message.
        logfire.error("Zwift disconnect failed", user_id=user_id, error=type(e).__name__)
        return DisconnectOutcome.FAILED

    if not response.is_success:
        logfire.error("Zwift disconnect failed", user_id=user_id, status_code=response.status_code)
        return DisconnectOutcome.FAILED
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or "disconnected" not in body:
        logfire.error("Zwift disconnect returned an unexpected body", user_id=user_id)
        return DisconnectOutcome.FAILED
    return DisconnectOutcome.REMOVED if body["disconnected"] else DisconnectOutcome.NO_LINK


def disconnect(user_id: str) -> bool:
    """Disconnect a user's Zwift account link in the service.

    For callers that only care whether a link was removed. An erasure, which must also
    know when the call failed, uses :func:`disconnect_link`.

    Args:
        user_id: The platform user identifier (stable primary key).

    Returns:
        True if a link existed and was removed, False otherwise (or on error).

    """
    return disconnect_link(user_id) is DisconnectOutcome.REMOVED


class RelinkOutcome(StrEnum):
    """What a :func:`relink_connection` call did."""

    # The link now belongs to the target (or the target already held the same Zwift account).
    MOVED = "moved"
    # The source holds no link. An older service without the endpoint answers the same way.
    NOT_FOUND = "not_found"
    # The target is linked to a different Zwift account; nothing changed.
    CONFLICT = "conflict"
    # The call failed or was refused (bad ids, bad key, service down).
    ERROR = "error"
    # The service connection is not configured, so nothing was asked.
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class RelinkResult:
    """The outcome of a relink, plus the zwid the service reported on success."""

    outcome: RelinkOutcome
    zwid: str | None = None


def relink_connection(from_user_id: str, to_user_id: str) -> RelinkResult:
    """Move a Zwift account link from one platform id to another in the service.

    A membership registration connects Zwift under its own UUID, because no User exists
    yet. This hands that link to the member's account without another trip through
    Zwift's consent page.

    Never raises: every failure comes back as an outcome, so an import cannot be broken
    by the service being down.

    Args:
        from_user_id: The id currently holding the link (a registration's UUID).
        to_user_id: The id that should hold it (the member's primary key).

    Returns:
        A :class:`RelinkResult`. ``zwid`` is set only on ``MOVED``.

    """
    if not is_configured():
        return RelinkResult(RelinkOutcome.UNCONFIGURED)
    try:
        response = httpx.post(
            _url("/api/zwift/oauth/relink"),
            json={"from_user_id": from_user_id, "to_user_id": to_user_id},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        # type(e) rather than str(e): httpx quotes the request URL in its message.
        logfire.error(
            "Zwift relink failed",
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            error=type(e).__name__,
        )
        return RelinkResult(RelinkOutcome.ERROR)

    status_code = response.status_code
    ids = {"from_user_id": from_user_id, "to_user_id": to_user_id, "status_code": status_code}
    if status_code == 404:
        logfire.info("Zwift relink found no link to move", **ids)
        return RelinkResult(RelinkOutcome.NOT_FOUND)
    if status_code == 409:
        logfire.warning("Zwift relink refused: target is linked to a different Zwift account", **ids)
        return RelinkResult(RelinkOutcome.CONFLICT)
    if status_code != 200:
        # 400 (ids equal) and 401 (bad key) are both our mistakes, so they are errors, not outcomes.
        logfire.error("Zwift relink failed", **ids)
        return RelinkResult(RelinkOutcome.ERROR)

    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not body.get("relinked"):
        logfire.error("Zwift relink returned an unexpected body", **ids)
        return RelinkResult(RelinkOutcome.ERROR)

    zwid = body.get("zwid")
    logfire.info("Zwift link relinked", **ids)
    return RelinkResult(RelinkOutcome.MOVED, zwid=str(zwid) if zwid is not None else None)
