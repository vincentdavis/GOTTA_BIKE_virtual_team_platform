"""HTTP client for the zauth unified rider-profile endpoints.

Separate from ``apps/zwift/client.py`` on purpose. That client is app-scoped: it uses the
per-app key, and every endpoint it touches is about *our* users -- their OAuth link, their
connection status. This one is zwid-scoped and uses the service key, because the whole point
is to fetch riders who are not our users: members who never linked Zwift, and eventually
scouted opponents.

Two keys, two reaches. Keeping the clients apart keeps that boundary visible rather than
leaving one module quietly holding both.
"""

from __future__ import annotations

import httpx
import logfire

from gotta_bike_platform.config import settings as config

# Generous next to the 15s used for single-user calls: this is a batch of hundreds of riders
# and the service merges three sources per rider before answering.
_TIMEOUT = 60.0

# The service builds one profile per zwid in a loop, so an unbounded list is a slow request
# and an all-or-nothing failure. Chunking bounds both.
_BATCH_SIZE = 200


def is_configured() -> bool:
    """Report whether the zwid-keyed batch endpoints can be called.

    Returns:
        True when both the service base URL and the *service* key are set. The per-app key
        is not sufficient -- it resolves to an app name and cannot request arbitrary zwids.

    """
    return bool(config.zwift_api_base_url and config.zwift_service_api_key)


def _url(path: str) -> str:
    """Build a full service URL from a path.

    Args:
        path: The endpoint path.

    Returns:
        The absolute URL against the configured service base.

    """
    return f"{(config.zwift_api_base_url or '').rstrip('/')}{path}"


def _headers() -> dict[str, str]:
    """Build request headers carrying the service key.

    Returns:
        Headers dict with the ``X-API-Key`` shared secret.

    """
    return {"X-API-Key": config.zwift_service_api_key or ""}


def fetch_profiles(zwids: list[int] | None = None, *, connected_app: str | None = None) -> list[dict]:
    """Fetch unified profiles for a set of riders.

    Either argument narrows the request and giving both intersects them, which the service
    handles: ``connected_app`` alone returns every rider linked to that app, and is how the
    connected set is obtained without maintaining a local copy of it.

    Riders the service holds no data for are simply absent from the response. That is not an
    error -- it is the answer -- so callers must not assume one profile per requested zwid.

    Args:
        zwids: Specific riders to fetch. Chunked internally.
        connected_app: Limit to riders whose account is linked to this app name.

    Returns:
        The profile documents returned, possibly fewer than requested. An empty list when
        the client is not configured or every chunk failed.

    """
    if not is_configured():
        logfire.warning("Rider profile batch skipped: service key or base URL not configured")
        return []

    zwids = sorted(set(zwids or []))
    # With no zwids and no app filter there is nothing to ask for, and an empty request
    # would return the service's entire rider table.
    if not zwids and not connected_app:
        logfire.warning("Rider profile batch skipped: neither zwids nor connected_app given")
        return []

    chunks: list[list[int]] = (
        [zwids[i : i + _BATCH_SIZE] for i in range(0, len(zwids), _BATCH_SIZE)] if zwids else [[]]
    )

    profiles: list[dict] = []
    with logfire.span("fetch_rider_profiles", requested=len(zwids), connected_app=connected_app):
        for chunk in chunks:
            payload: dict = {"zwids": chunk}
            if connected_app:
                payload["connected_app"] = connected_app
            try:
                response = httpx.post(
                    _url("/api/riders/profiles-full"),
                    json=payload,
                    headers=_headers(),
                    timeout=_TIMEOUT,
                )
                response.raise_for_status()
                profiles.extend(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                # One bad chunk must not lose the others. A partial refresh leaves some rows
                # stale, which is visible through fetched_at; losing the whole run is not.
                logfire.error(
                    "Rider profile batch chunk failed",
                    error=str(exc),
                    chunk_size=len(chunk),
                    connected_app=connected_app,
                )

        logfire.info("Fetched rider profiles", requested=len(zwids), returned=len(profiles))
    return profiles
