"""Project-wide pytest fixtures.

Conventions:
- Use `db` (built-in pytest-django fixture) on any test that touches the database.
- The `client` and `admin_client` fixtures come from pytest-django.
- Permission fixtures (`team_member`, `app_admin`, etc.) grant access via
  ``User.permission_overrides`` so tests do not depend on Constance/Discord roles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from apps.accounts.models import User as UserType


@pytest.fixture
def user_model() -> type[AbstractUser]:
    """Return the active User model class."""
    return get_user_model()


def _make_user(
    user_model: type[AbstractUser],
    *,
    username: str,
    permissions: dict[str, bool] | None = None,
    **extra: object,
) -> UserType:
    """Create a User with optional permission overrides."""
    defaults: dict[str, object] = {
        "email": f"{username}@example.test",
        "first_name": username.title(),
        "last_name": "Test",
    }
    if permissions:
        defaults["permission_overrides"] = dict(permissions)
    defaults.update(extra)
    user = user_model.objects.create_user(username=username, **defaults)
    return user  # type: ignore[return-value]


@pytest.fixture
def user(db, user_model) -> UserType:
    """Plain authenticated user with no special permissions."""
    return _make_user(user_model, username="plain_user")


@pytest.fixture
def team_member(db, user_model) -> UserType:
    """User with the ``team_member`` permission granted via override."""
    return _make_user(
        user_model,
        username="team_member",
        permissions={"team_member": True},
    )


@pytest.fixture
def app_admin(db, user_model) -> UserType:
    """User with ``app_admin`` (implies most things via has_permission)."""
    return _make_user(
        user_model,
        username="app_admin",
        permissions={"app_admin": True, "team_member": True},
    )


@pytest.fixture
def event_admin(db, user_model) -> UserType:
    """User with ``event_admin`` permission."""
    return _make_user(
        user_model,
        username="event_admin",
        permissions={"event_admin": True, "team_member": True},
    )


@pytest.fixture
def superuser(db, user_model) -> UserType:
    """Django superuser — bypasses all permission checks."""
    return _make_user(
        user_model,
        username="super",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def auth_client(client, team_member):
    """Test client logged in as a team_member."""
    client.force_login(team_member)
    return client


@pytest.fixture
def admin_authed_client(client, app_admin):
    """Test client logged in as an app_admin."""
    client.force_login(app_admin)
    return client


# --- Race-ready / verification fixtures -------------------------------------


@pytest.fixture
def zp_team_rider_factory(db):
    """Build a ZPTeamRiders row. Defaults to a Cat B (div=20) male rider."""
    from apps.zwiftpower.models import ZPTeamRiders

    counter = {"n": 9_000_000}

    def _make(
        *,
        zwid: int | None = None,
        div: int = 20,
        divw: int = 0,
        name: str = "Test Rider",
    ):
        if zwid is None:
            counter["n"] += 1
            zwid = counter["n"]
        return ZPTeamRiders.objects.create(zwid=zwid, div=div, divw=divw, name=name)

    return _make


@pytest.fixture
def verification_factory(db):
    """Build a RaceReadyRecord for a given user.

    Defaults: status=verified, record_date=today, url set so clean() would pass.
    """
    from datetime import date, timedelta

    from django.utils import timezone

    from apps.team.models import RaceReadyRecord

    def _make(
        user,
        verify_type: str,
        *,
        status: str = RaceReadyRecord.Status.VERIFIED,
        record_date: date | None = None,
        days_ago: int | None = None,
        url: str = "https://example.test/evidence",
        weight: float | None = None,
        height: int | None = None,
        ftp: int | None = None,
    ):
        if record_date is None:
            anchor = timezone.now().date()
            record_date = anchor - timedelta(days=days_ago) if days_ago is not None else anchor
        return RaceReadyRecord.objects.create(
            user=user,
            verify_type=verify_type,
            media_type="link",
            url=url,
            status=status,
            record_date=record_date,
            weight=weight,
            height=height,
            ftp=ftp,
        )

    return _make
