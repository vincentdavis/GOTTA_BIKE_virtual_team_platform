"""Fixtures for the TTT planner's tests."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def route_profiles():
    """Stand in for the Speed Lab catalog's route profiles, empty unless a test adds one.

    Without this, any plan with a route would read ``route_profiles.json`` from whatever
    media directory the test run points at. Tests add profiles by ``(world_id, name_hash)``.

    Yields:
        The profiles dict the catalog lookup reads.

    """
    profiles: dict[tuple[int, str], dict] = {}
    with patch(
        "apps.zwift_data.catalog.route_profile",
        side_effect=lambda world_id, name_hash: profiles.get((world_id, name_hash)),
    ):
        yield profiles
