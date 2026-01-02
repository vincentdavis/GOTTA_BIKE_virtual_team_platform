from urllib.parse import urlencode

import httpx
import logfire


def fetch_zwift_id(username: str, password: str) -> str | None:
    """Fetch Zwift ID from the Sauce mod API using Zwift credentials.

    This is a Python equivalent of the JavaScript function that calls:
    https://z00pbp8lig.execute-api.us-west-1.amazonaws.com/latest/zwiftId

    Args:
        username: Zwift account username/email
        password: Zwift account password

    Returns:
        The Zwift ID as a string if successful, None if there was an error

    """
    api_url = "https://z00pbp8lig.execute-api.us-west-1.amazonaws.com/latest/zwiftId"
    params = urlencode({"username": username, "pw": password})
    full_url = f"{api_url}?{params}"

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(full_url)
            response.raise_for_status()
            zwift_id = response.text.strip()
            logfire.info(f"Successfully fetched Zwift ID for user: {username}")
            return zwift_id
    except httpx.TimeoutException:
        logfire.error(f"Timeout fetching Zwift ID for user: {username}")
        return None
    except httpx.HTTPStatusError as e:
        logfire.error(f"HTTP error fetching Zwift ID: {e.response.status_code}")
        return None
    except Exception as e:
        logfire.error(f"Error fetching Zwift ID: {e!s}")
        return None