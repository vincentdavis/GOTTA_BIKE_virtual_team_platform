"""Integration tests for ZPClient using real credentials from .env."""

from apps.zwiftpower.zp_client import ZPClient


class TestZPClientIntegration:
    """Integration tests that connect to the real ZwiftPower service."""

    def test_login_and_check_status(self):
        """Test login with real credentials and verify session is valid."""
        client = ZPClient()

        # Login should succeed with valid credentials from .env
        client.login()

        # Session should be established
        assert client.session is not None

        # Status check should return True for authenticated session
        assert client.check_status() is True

        # Clean up
        client.close()
        assert client.session is None

    def test_login_with_context_manager(self):
        """Test login using context manager for automatic cleanup."""
        with ZPClient() as client:
            client.login()

            assert client.session is not None
            assert client.check_status() is True

        # Session should be closed after exiting context
        assert client.session is None

    def test_init_client_establishes_session(self):
        """Test init_client method establishes a valid session."""
        with ZPClient() as client:
            session = client.init_client()

            assert session is not None
            assert client.check_status() is True

    def test_check_status_returns_false_before_login(self):
        """Test check_status returns False when not logged in."""
        client = ZPClient()

        # Before login, status should be False
        assert client.check_status() is False

        client.close()

    def test_close_is_idempotent(self):
        """Test that close can be called multiple times safely."""
        with ZPClient() as client:
            client.login()
            assert client.session is not None

            # First close
            client.close()
            assert client.session is None

            # Second close should not raise
            client.close()
            assert client.session is None

    def test_relogin_after_close(self):
        """Test that the client can log in again after closing."""
        client = ZPClient()

        # First login
        client.login()
        assert client.check_status() is True

        # Close session
        client.close()
        assert client.session is None

        # Login again
        client.login()
        assert client.check_status() is True

        # Clean up
        client.close()

    def test_fetch_team_riders(self):
        """Test fetching team riders returns list of team members."""
        with ZPClient() as client:
            roster = client.fetch_team_riders()

            # Should return a list
            assert isinstance(roster, list)

            # Team should have members
            assert len(roster) > 0

            # Each member should have expected fields
            member = roster[0]
            assert "zwid" in member
            assert "name" in member
            assert "flag" in member
