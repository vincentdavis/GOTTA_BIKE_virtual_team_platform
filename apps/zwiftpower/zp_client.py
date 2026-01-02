"""ZwiftPower API client for authentication and session management."""

from time import sleep
from typing import Self

import httpx
import logfire
from bs4 import BeautifulSoup
from constance import config


class ZPLoginError(Exception):
    """Raised when login to ZwiftPower fails."""


class ZPFormParseError(Exception):
    """Raised when unable to parse login form from ZwiftPower."""


class ZPClient:
    """Client for interacting with ZwiftPower API.

    Other APIs to add:
        team_riders: {zp_url}/api3.php?do=team_riders&id={id}
        team_pending: {zp_url}/api3.php?do=team_pending&id={id}
        team_results: {zp_url}/api3.php?do=team_results&id={id}
        profile_profile: {zp_url}/cache3/profile/{id}_all.json
    """

    def __init__(self) -> None:
        """Initialize ZPClient with credentials from constance config."""
        self._username = config.ZWIFT_USERNAME
        self._password = config.ZWIFT_PASSWORD
        self.zp_url = "https://zwiftpower.com"
        self.zp_events_url = "https://zwiftpower.com/events.php"
        self._session: httpx.Client | None = None
        # User Agent required or will be blocked at some apis
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"
        )

    @property
    def session(self) -> httpx.Client | None:
        """Return the current session."""
        return self._session

    def close(self) -> None:
        """Close the httpx session and release resources."""
        if self._session is not None:
            self._session.close()
            self._session = None
            logfire.info("ZPClient session closed")

    def __enter__(self) -> Self:
        """Enter context manager.

        Returns:
            The ZPClient instance.

        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager and close session."""
        self.close()

    def check_status(self) -> bool:
        """Check if the session is valid.

        Returns:
            True if session is valid and authenticated, False otherwise.

        """
        try:
            if self._session is None:
                return False

            r = self._session.get(self.zp_url)
            r.raise_for_status()
            login_required = "Login Required" in r.text
            logfire.info(f"Status: {r.status_code} Login Required: {login_required}")

            sleep(1)

            r = self._session.get(self.zp_events_url)
            status_ok = r.status_code == 200
            return status_ok and not login_required

        except httpx.RequestError as e:
            logfire.error(f"Request error checking status: {e}")
            return False
        except httpx.HTTPStatusError as e:
            logfire.error(f"HTTP error checking status: {e}")
            return False

    def _parse_login_form_url(self, html: str) -> str:
        """Parse the login form action URL from HTML.

        Args:
            html: HTML content containing the login form.

        Returns:
            The form action URL.

        Raises:
            ZPFormParseError: If form or action attribute not found.

        """
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")

        if form is None:
            raise ZPFormParseError("Login form not found in response")

        action = form.get("action")
        if not action:
            raise ZPFormParseError("Login form has no action attribute")

        return str(action)

    def _validate_login_response(self, response: httpx.Response) -> None:
        """Validate that login was successful.

        Args:
            response: The response from the login POST request.

        Raises:
            ZPLoginError: If login validation fails.

        """
        url_str = str(response.url)
        response_text = response.text.lower()

        # Check if still on Zwift secure login page (login failed)
        if "https://secure.zwift.com/" in url_str:
            raise ZPLoginError("Login failed: still on Zwift secure login page")

        # Check if redirected to events page (login succeeded)
        if "https://zwiftpower.com/events.php" not in url_str:
            raise ZPLoginError(f"Login failed: unexpected redirect to {url_str}")

        # Check for invalid credentials message
        if "invalid username or password" in response_text:
            raise ZPLoginError("Login failed: invalid username or password")

    def login(self) -> None:
        """Login to ZwiftPower and establish a session.

        Raises:
            ZPLoginError: If login fails.
            ZPFormParseError: If unable to parse login form.

        """
        # Close existing session if any
        self.close()

        client = httpx.Client()
        client.headers.update({"User-Agent": self.user_agent})

        try:
            # Initial request to ZwiftPower
            resp = client.get(self.zp_url)
            logfire.info(f"Init login status: {resp.status_code}")

            # Get OAuth login page
            oauth_url = f"{self.zp_url}/ucp.php?mode=login&login=external&oauth_service=oauthzpsso"
            r2 = client.get(oauth_url, follow_redirects=True)

            # Parse form action URL
            post_url = self._parse_login_form_url(r2.text)
            logfire.info(f"Post URL: {post_url}")

            # Submit login credentials
            login_data = {
                "username": self._username,
                "password": self._password,
                "rememberMe": "on",
            }
            logfire.info(f"Attempting login for user: {self._username}")

            r3 = client.post(post_url, data=login_data, follow_redirects=True)
            logfire.info(f"Post LOGIN status: {r3.status_code}")

            # Validate login succeeded
            self._validate_login_response(r3)

            logfire.info("Login successful, session created")
            self._session = client

        except ZPLoginError, ZPFormParseError:
            client.close()
            self._session = None
            raise
        except Exception as e:
            client.close()
            self._session = None
            logfire.error(f"Unexpected error during login: {e}")
            raise ZPLoginError(f"Login failed: {e}") from e

    def init_client(self) -> httpx.Client | None:
        """Get a valid session, logging in if necessary.

        Returns:
            An authenticated httpx.Client, or None if login fails.

        """
        with logfire.span("ZPClient:init_client"):
            if self.check_status():
                logfire.info("Existing session is valid")
                return self._session

            logfire.info("Session is not valid, attempting login")
            try:
                self.login()
                return self._session
            except (ZPLoginError, ZPFormParseError) as e:
                logfire.error(f"Failed to login to ZwiftPower: {e}")
                return None

    def fetch_team_riders(self, team_id: int | None = None) -> list[dict]:
        """Fetch the team roster from ZwiftPower. The logged in user should be a team admin.

        team_riders url: https://zwiftpower.com/api3.php?do=team_riders&id=11991

        returns the list of team_riders
        """
        self.init_client()
        if team_id is None:
            team_id = config.ZWIFTPOWER_TEAM_ID
        url = f"https://zwiftpower.com/api3.php?do=team_riders&id={team_id}"
        response = self._session.get(url)
        response.raise_for_status()

        try:
            data: list = response.json()["data"]
            return data
        except Exception as e:
            logfire.error(f"Error fetching team roster, no data or json error: {e}")
            return []

    def fetch_team_results(self, team_id: int | None = None) -> dict:
        """Fetch team results from ZwiftPower.

        API URL: https://zwiftpower.com/api3.php?do=team_results&id={team_id}

        Returns:
            Dict with 'events' (dict of event info) and 'data' (list of rider results).

        """
        self.init_client()
        if team_id is None:
            team_id = config.ZWIFTPOWER_TEAM_ID
        url = f"https://zwiftpower.com/api3.php?do=team_results&id={team_id}"
        response = self._session.get(url)
        response.raise_for_status()

        try:
            data: dict = response.json()
            return {
                "events": data.get("events", {}),
                "data": data.get("data", []),
            }
        except Exception as e:
            logfire.error(f"Error fetching team results, no data or json error: {e}")
            return {"events": {}, "data": []}
