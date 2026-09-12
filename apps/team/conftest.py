"""Shared fixtures for team tests."""

import pytest


@pytest.fixture
def roster_rider(db, rider_profile_factory, zp_team_rider_factory):
    """Build a rider who is BOTH cached and on the team, which is what puts them on the roster.

    Two rows, because the roster is an intersection: ``build_roster_index`` shows a rider only
    when the cache holds their stats AND ``zwids_to_refresh()`` still counts them as racing for
    this team. A cached rider with no team row is somebody we hold data on who has left or was
    never here, and they get no card -- so a fixture that made only the cache row would quietly
    test an empty roster.

    Returns:
        A callable taking the same keywords as ``rider_profile_factory``, returning the
        ``RiderProfile``.

    """

    def _make(*, zwid: int, **kwargs):
        zp_team_rider_factory(zwid=zwid, name=kwargs.get("name", "Test Rider"))
        return rider_profile_factory(zwid=zwid, **kwargs)

    return _make
