"""HTTP client for the zwiftgopher.com TTT optimize API.

Thin wrapper around the single ``POST /api/optimize`` endpoint. Returns a
``(status_code, json)`` tuple like the other external clients in this project.
The API is rate-limited to 1 request / 60 s per key+IP; throttling is handled by
the caller (the background task), not here.
"""

from __future__ import annotations

import httpx
import logfire

from gotta_bike_platform.config import settings

BASE_URL = "https://zwiftgopher.com"
OPTIMIZE_PATH = "/api/optimize"
# The API fetches rider data then optimizes; docs suggest ~90 s. Allow headroom.
REQUEST_TIMEOUT = 100.0


def is_configured() -> bool:
    """Return whether a zwiftgopher API key is configured.

    Returns:
        True if a key is present in settings.

    """
    return bool(settings.zwift_gopher_api)


def optimize(payload: dict) -> tuple[int, dict]:
    """Call the zwiftgopher optimize endpoint.

    Args:
        payload: The request body (single or batch optimize request).

    Returns:
        A ``(status_code, json)`` tuple. On a 429 the JSON includes whatever the
        API returned (rate-limit info). On a transport error returns
        ``(0, {"error": ...})``.

    """
    if not is_configured():
        return 0, {"error": "zwiftgopher API key not configured"}

    headers = {
        "Authorization": f"Bearer {settings.zwift_gopher_api}",
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{OPTIMIZE_PATH}"
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logfire.error("zwiftgopher request failed", error=str(exc))
        return 0, {"error": str(exc)}

    try:
        data = response.json()
    except ValueError:
        data = {"error": "non-JSON response", "body": response.text[:500]}

    if response.status_code == 429:
        logfire.warning("zwiftgopher rate limited", reset=response.headers.get("X-RateLimit-Reset"))
    elif response.status_code >= 400:
        logfire.error("zwiftgopher error response", status_code=response.status_code)

    return response.status_code, data
