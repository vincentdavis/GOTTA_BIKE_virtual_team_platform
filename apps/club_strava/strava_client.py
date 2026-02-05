"""Strava API client for club activities.

Provides methods to interact with the Strava Club Activities API.
Requires OAuth access token with activity:read scope.
The authenticated user must be a member of the club.
"""

import httpx
import logfire
from constance import config
from django.db import transaction

from apps.club_strava.models import ClubActivity

STRAVA_API_BASE = "https://www.strava.com/api/v3"
STRAVA_OAUTH_URL = "https://www.strava.com/oauth/token"


def _get_headers() -> dict[str, str]:
    """Get the API headers with authorization from constance config.

    Returns:
        Dictionary with Authorization header.

    """
    return {"Authorization": f"Bearer {config.STRAVA_ACCESS_TOKEN}"}


def refresh_access_token() -> bool:
    """Refresh the Strava access token using the refresh token.

    Updates STRAVA_ACCESS_TOKEN and STRAVA_REFRESH_TOKEN in constance config.

    Returns:
        True if refresh was successful, False otherwise.

    """
    client_id = config.STRAVA_CLIENT_ID
    client_secret = config.STRAVA_CLIENT_SECRET
    refresh_token = config.STRAVA_REFRESH_TOKEN

    if not all([client_id, client_secret, refresh_token]):
        logfire.error(
            "Missing Strava credentials for token refresh",
            has_client_id=bool(client_id),
            has_client_secret=bool(client_secret),
            has_refresh_token=bool(refresh_token),
            client_id_type=type(client_id).__name__,
            client_id_value=client_id[:4] + "..." if client_id else "(empty)",
        )
        return False

    try:
        response = httpx.post(
            STRAVA_OAUTH_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )

        logfire.debug(
            "Strava token refresh response",
            status_code=response.status_code,
            response_text=response.text[:500] if response.text else "(empty)",
        )

        if response.status_code != 200:
            # Try to parse error details from response
            try:
                error_data = response.json()
            except Exception:
                error_data = response.text

            logfire.error(
                "Strava token refresh failed",
                status_code=response.status_code,
                error_data=error_data,
            )
            return False

        data = response.json()
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")

        if not new_access_token:
            logfire.error("No access token in refresh response", response=data)
            return False

        # Update constance config with new tokens
        from constance.models import Constance

        Constance.objects.update_or_create(
            key="STRAVA_ACCESS_TOKEN",
            defaults={"value": f'"{new_access_token}"'},
        )

        if new_refresh_token:
            Constance.objects.update_or_create(
                key="STRAVA_REFRESH_TOKEN",
                defaults={"value": f'"{new_refresh_token}"'},
            )

        logfire.info("Strava access token refreshed successfully")
        return True

    except httpx.RequestError as e:
        logfire.error("Strava token refresh request failed", error=str(e))
        return False
    except Exception as e:
        logfire.error("Strava token refresh unexpected error", error=str(e), error_type=type(e).__name__)
        return False


def _handle_response(response: httpx.Response, endpoint: str) -> tuple[int, dict | list]:
    """Handle API response.

    Args:
        response: The httpx Response object.
        endpoint: The API endpoint for logging context.

    Returns:
        Tuple of (status_code, response_json).

    """
    if response.status_code == 429:
        logfire.warning(
            "Strava API rate limited",
            endpoint=endpoint,
            headers=dict(response.headers),
        )
        return response.status_code, {"error": "rate_limited"}

    if response.status_code == 401:
        logfire.warning(
            "Strava API authentication failed - token may need refresh",
            endpoint=endpoint,
        )
        return response.status_code, {"error": "unauthorized"}

    response.raise_for_status()
    return response.status_code, response.json()


def get_club_activities(
    club_id: int | None = None,
    page: int = 1,
    per_page: int = 30,
    _retry_after_refresh: bool = True,
) -> tuple[int, list | dict]:
    """Get recent activities from a Strava club.

    Args:
        club_id: The Strava club ID. If None, uses STRAVA_CLUB_ID from config.
        page: Page number for pagination (default: 1).
        per_page: Number of activities per page (default: 30, max: 200).
        _retry_after_refresh: Internal flag to prevent infinite retry loops.

    Returns:
        Tuple of (status_code, response_json).
        On success, response_json is a list of activity summaries.
        On error, response_json is a dict with error info.

    """
    if club_id is None:
        club_id = config.STRAVA_CLUB_ID

    if not club_id:
        logfire.error("Strava club ID not configured")
        return 400, {"error": "club_id_not_configured"}

    endpoint = f"clubs/{club_id}/activities"
    url = f"{STRAVA_API_BASE}/{endpoint}"
    params = {"page": page, "per_page": min(per_page, 200)}

    logfire.debug("Strava API request: get_club_activities", club_id=club_id, page=page, per_page=per_page)

    try:
        response = httpx.get(url, headers=_get_headers(), params=params, timeout=30.0)
        status_code, data = _handle_response(response, endpoint)

        # If unauthorized and we haven't tried refreshing yet, try to refresh token
        if status_code == 401 and _retry_after_refresh:
            logfire.info("Attempting to refresh Strava access token")
            if refresh_access_token():
                # Retry the request with the new token
                return get_club_activities(
                    club_id=club_id,
                    page=page,
                    per_page=per_page,
                    _retry_after_refresh=False,
                )
            else:
                logfire.error("Token refresh failed, cannot retry request")

        return status_code, data

    except httpx.RequestError as e:
        logfire.error("Strava API request failed", endpoint=endpoint, error=str(e))
        return 500, {"error": str(e)}


def sync_club_activities(club_id: int | None = None, pages: int = 1) -> dict:
    """Fetch club activities from Strava and store in database.

    Args:
        club_id: The Strava club ID. If None, uses STRAVA_CLUB_ID from config.
        pages: Number of pages to fetch (default: 1).

    Returns:
        Dict with sync results: created, updated, errors counts.

    """
    if club_id is None:
        club_id = config.STRAVA_CLUB_ID

    results = {"created": 0, "updated": 0, "errors": 0, "total_fetched": 0}

    for page in range(1, pages + 1):
        status_code, data = get_club_activities(club_id=club_id, page=page, per_page=200)

        if status_code != 200:
            logfire.error("Failed to fetch club activities", status_code=status_code, page=page, data=data)
            results["errors"] += 1
            break

        if not data:
            # No more activities
            break

        results["total_fetched"] += len(data)

        with transaction.atomic():
            for activity in data:
                try:
                    # Parse activity date (Strava returns ISO 8601 format)
                    activity_date = None
                    date_str = activity.get("start_date_local") or activity.get("start_date")
                    if date_str:
                        from datetime import datetime

                        try:
                            # Handle ISO 8601 format with Z suffix
                            activity_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        except ValueError:
                            logfire.warning("Failed to parse activity date", date_str=date_str)

                    # Extract activity data - Strava uses different field names
                    activity_data = {
                        "athlete_first_name": activity.get("athlete", {}).get("firstname", "Unknown"),
                        "athlete_last_name": activity.get("athlete", {}).get("lastname", ""),
                        "name": activity.get("name", "Untitled"),
                        "sport_type": activity.get("sport_type", activity.get("type", "Unknown")),
                        "workout_type": activity.get("workout_type"),
                        "distance": activity.get("distance", 0),
                        "moving_time": activity.get("moving_time", 0),
                        "elapsed_time": activity.get("elapsed_time", 0),
                        "total_elevation_gain": activity.get("total_elevation_gain"),
                        "activity_date": activity_date,
                    }

                    # Club activities don't have activity IDs, generate one from name + athlete + time
                    activity_id = activity.get("id")
                    if not activity_id:
                        # Generate a pseudo-ID from the activity data
                        import hashlib

                        hash_input = (
                            f"{activity_data['name']}-"
                            f"{activity_data['athlete_first_name']}-"
                            f"{activity_data['moving_time']}"
                        )
                        activity_id = int(hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest()[:15], 16)

                    _obj, created = ClubActivity.objects.update_or_create(
                        strava_id=activity_id,
                        defaults=activity_data,
                    )

                    if created:
                        results["created"] += 1
                    else:
                        results["updated"] += 1

                except Exception as e:
                    logfire.error("Failed to save activity", activity=activity, error=str(e))
                    results["errors"] += 1

    logfire.info(
        "Strava club activities sync complete",
        club_id=club_id,
        created=results["created"],
        updated=results["updated"],
        errors=results["errors"],
        total_fetched=results["total_fetched"],
    )

    return results
