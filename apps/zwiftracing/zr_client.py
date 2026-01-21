"""Zwift Racing API client.

Provides methods to interact with the Zwift Racing API endpoints.
All methods return a tuple of (status_code, response_json).
429 errors are returned without raising an exception to allow retry handling.
"""

import httpx
import logfire
from constance import config


def _get_api_url() -> str:
    """Get the ZRAPP API URL from constance config.

    Returns:
        The API base URL string.

    """
    return config.ZRAPP_API_URL


def _get_headers() -> dict[str, str]:
    """Get the API headers with authorization from constance config.

    Returns:
        Dictionary with Authorization header.

    """
    return {"Authorization": config.ZRAPP_API_KEY}


def _handle_response(response: httpx.Response, endpoint: str) -> tuple[int, dict]:
    """Handle API response, allowing 429 through without raising.

    Args:
        response: The httpx Response object.
        endpoint: The API endpoint for logging context.

    Returns:
        Tuple of (status_code, response_json).
        Non-success status codes (except 429) will raise httpx.HTTPStatusError.

    """
    if response.status_code == 429:
        data = response.json()
        retry_after = data.get("retryAfter", "unknown")
        logfire.warning(
            "ZR API rate limited",
            endpoint=endpoint,
            retry_after=retry_after,
        )
        return response.status_code, data
    response.raise_for_status()
    return response.status_code, response.json()


# CLUB ============================================================================================
# Returns a dictionary containing club details and riders
# - e.g. get_club(20650)
# - Given a from_id, it returns riders starting from the from_id, e.g. get_club(20650, 4598636)
def get_club(club_id: int, from_id: int | None = None) -> tuple[int, dict]:
    """Get club details and riders.

    Args:
        club_id: The club ID.
        from_id: Optional rider ID to paginate from.

    Returns:
        Tuple of (status_code, response_json). On 429, returns the rate limit info.

    """
    endpoint = f"clubs/{club_id}"
    logfire.debug("ZR API request: get_club", club_id=club_id, from_id=from_id)
    response = httpx.get(url=f"{_get_api_url()}clubs/{club_id}/{from_id if from_id else ''}", headers=_get_headers())
    return _handle_response(response, endpoint)


# EVENT ===========================================================================================
# Returns a dictionary contain event details and ordered list of riders (not like riders endpoint) in results
# - e.g. get_event(5188741)
def get_event(event_id: int) -> tuple[int, dict]:
    """Get event details and results.

    Args:
        event_id: The event ID.

    Returns:
        Tuple of (status_code, response_json). On 429, returns the rate limit info.

    """
    endpoint = f"results/{event_id}"
    logfire.debug("ZR API request: get_event", event_id=event_id)
    response = httpx.get(url=f"{_get_api_url()}results/{event_id}", headers=_get_headers())
    return _handle_response(response, endpoint)


# Returns a dictionary with a zwiftpower set of riders in finishing order.
# Includes rider data: ID, name, club, weight, height, power, times.
# - e.g. get_zp_results(5188741)
def get_zp_results(event_id: int) -> tuple[int, dict]:
    """Get ZwiftPower-style results for an event.

    Args:
        event_id: The event ID.

    Returns:
        Tuple of (status_code, response_json). On 429, returns the rate limit info.

    """
    endpoint = f"zp/{event_id}/results"
    logfire.debug("ZR API request: get_zp_results", event_id=event_id)
    response = httpx.get(url=f"{_get_api_url()}zp/{event_id}/results", headers=_get_headers())
    return _handle_response(response, endpoint)


# RIDER(S) ========================================================================================
# Returns a dictionary of the rider details
# - e.g. get_rider(4598636)
# - Given epoch, it returns details for that time point, e.g. get_rider(4598636, 1733011200)
def get_rider(rider_id: int, epoch: int | None = None) -> tuple[int, dict]:
    """Get rider details.

    Args:
        rider_id: The rider ID.
        epoch: Optional epoch timestamp to get historical data.

    Returns:
        Tuple of (status_code, response_json). On 429, returns the rate limit info.

    """
    endpoint = f"riders/{rider_id}"
    logfire.debug("ZR API request: get_rider", rider_id=rider_id, epoch=epoch)
    response = httpx.get(url=f"{_get_api_url()}riders/{rider_id}/{epoch if epoch else ''}", headers=_get_headers())
    return _handle_response(response, endpoint)


# Returns a list of rider-dictionaries
# - e.g. get_riders([4598636, 5574])
# - Given epoch, it returns details for that time point, e.g. get_riders([4598636, 5574], 1733011200)
def get_riders(ids: list[int], epoch: int | None = None) -> tuple[int, list | dict]:
    """Get multiple riders' details.

    Args:
        ids: List of rider IDs.
        epoch: Optional epoch timestamp to get historical data.

    Returns:
        Tuple of (status_code, response_json). On 429, returns the rate limit info (dict).

    """
    endpoint = "riders (batch)"
    logfire.debug("ZR API request: get_riders", rider_count=len(ids), epoch=epoch)
    response = httpx.post(url=f"{_get_api_url()}riders/{epoch if epoch else ''}", headers=_get_headers(), json=ids)
    return _handle_response(response, endpoint)


# if __name__ == "__main__":
#     ZRAPP_API_URL = "https://api.zwiftracing.app/api/public/"

#
#     id = 11991
#     status, club = get_club(id)
#     print(f"Status: {status})")
#     print("####")
#     print(club)
