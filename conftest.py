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


@pytest.fixture(autouse=True)  # must apply to every test that renders templates
def _use_plain_static_storage(settings):
    """Swap WhiteNoise manifest storage for plain storage during tests.

    `CompressedManifestStaticFilesStorage` reads a manifest produced by
    ``collectstatic``; that file doesn't exist in CI / pytest runs, so any
    test that renders ``base.html`` would otherwise fail when it resolves
    static asset URLs.
    """
    from django.utils.functional import empty

    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    # Reset the LazyObject so the next access rebuilds with the new backend.
    from django.contrib.staticfiles.storage import staticfiles_storage

    staticfiles_storage._wrapped = empty


@pytest.fixture(autouse=True)
def _fresh_roster_index():
    """Start every test with no shared roster index.

    ``apps.team.rosterv2.shared_roster_index`` keeps the built roster in the process for a
    minute, which in a test run means one test's riders would still be on the next test's
    page. Autouse and site-wide because any test that renders /team/roster/ reads it.
    """
    from apps.team.rosterv2 import reset_roster_index_cache

    reset_roster_index_cache()
    yield
    reset_roster_index_cache()


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
    return user_model.objects.create_user(username=username, **defaults)  # type: ignore[return-value]


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
def membership_admin(db, user_model) -> UserType:
    """User with ``membership_admin`` permission."""
    return _make_user(
        user_model,
        username="membership_admin",
        permissions={"membership_admin": True, "team_member": True},
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
def complete_profile(db):
    """Return a function that fills in every field ``User.is_profile_complete`` requires.

    Events require a complete profile to sign up by default
    (``Event.require_complete_profile_signup``), so a test that signs a rider up -- and is
    not about that requirement -- uses this to meet it. Fields already set are kept. The
    Zwift verification is through zauth, so the profile stays complete even with the
    ``ZAUTH_VERIFICATION_REQUIRED`` cutover on.

    Returns:
        ``fill(user) -> user``, saving the user.

    """

    def fill(user: UserType) -> UserType:
        user.first_name = user.first_name or "Test"
        user.last_name = user.last_name or "Rider"
        user.gender = user.gender or "female"
        user.timezone = user.timezone or "UTC"
        user.country = user.country or "US"
        user.birth_year = user.birth_year or 1990
        user.trainer = user.trainer or "Smart trainer"
        user.heartrate_monitor = user.heartrate_monitor or "Chest strap"
        user.zwid = user.zwid or 1_000_000 + user.pk
        user.zwid_verified = True
        user.zwid_verification_method = "zauth"
        user.save()
        if not user.is_profile_complete:
            pytest.fail("complete_profile left a required field empty -- has is_profile_complete gained one?")
        return user

    return fill


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
def rider_profile_factory(db):
    """Build a RiderProfile from a realistic zauth document, the way the sync does.

    Goes through ``services.store_profiles`` rather than ``objects.create`` so the columns and
    the payload are filled by the same mapping production uses. A factory writing columns
    directly keeps passing after that mapping moves, and the roster reads both halves.

    Defaults describe a rider with data in every block, weight and height included -- the
    roster must hold rows that HAVE those values and still never show them, so a factory that
    left them blank would make the allow-list tests pass for the wrong reason.

    Pass any zauth block by name to override or extend it (``identity={"name": "X"}``,
    ``physical={}``); dicts merge one level deep, anything else replaces.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.rider_data import services
    from apps.rider_data.models import RiderProfile

    counter = {"n": 7_000_000}

    def _make(
        *,
        zwid: int | None = None,
        name: str = "Test Rider",
        gender: str = "male",
        country: str = "US",
        age: str = "Vet",
        category_open: str = "B",
        category_women: str = "",
        category_racing: str = "Gold",
        phenotype: str = "Sprinter",
        velo: float | None = 1580.0,
        zwift_racing_score: float | None = 420.0,
        ftp: float | None = 250.0,
        weight_kg: float | None = 72.0,
        height_cm: float | None = 178.0,
        wkg_20min: float | None = 3.5,
        wkg_1min: float | None = 5.6,
        days_since_race: int | None = 2,
        **blocks,
    ):
        if zwid is None:
            counter["n"] += 1
            zwid = counter["n"]

        # last_race_at is DERIVED from clubs.known[].last_seen, so a test wanting a stale or
        # never-raced rider says so here rather than writing the column afterwards.
        known = []
        if days_since_race is not None:
            seen = (timezone.now() - timedelta(days=days_since_race)).date().isoformat()
            known = [{"id": 77, "name": "The Coalition", "last_seen": seen, "race_count": 9}]

        watts_20min = round(wkg_20min * weight_kg) if wkg_20min and weight_kg else None
        doc = {
            "zwid": zwid,
            "zwift_user_id": f"uuid-{zwid}",
            "identity": {"name": name, "gender": gender, "country": country, "age": age},
            "physical": {"weight_kg": weight_kg, "height_cm": height_cm},
            "power": {
                "ftp": ftp,
                "zftp": ftp,
                # Curves are keyed by DURATION IN SECONDS -- "60" is a minute, "1200" twenty --
                # and upstream builds them from the ZwiftRacing row only, so a rider with no ZR
                # data has no curve at all rather than an empty one.
                "curve_wkg": {"60": wkg_1min, "1200": wkg_20min},
                "curve_w": {"1200": watts_20min},
            },
            "category": {"open": category_open, "women": category_women, "racing": category_racing},
            "ratings": {
                "velo": velo,
                "zwift_racing_score": zwift_racing_score,
                "rating_max30": None if velo is None else velo + 20,
                "rating_max90": None if velo is None else velo + 60,
            },
            "phenotype": {"value": phenotype, "scores": {"sprinter": 80, "climber": 40}},
            # distance_km is MISNAMED upstream and carries metres; the model converts it.
            "totals": {"distance_km": 48_200_000, "climbed_m": 512_000},
            "clubs": {"current": {"id": 77, "name": "The Coalition"}, "known": known},
            "sources": {"zwiftpower": {"present": True, "fetched_at": "2026-09-01T10:00:00Z"}},
            "has_account": {"zwift_api": True, "zwiftpower": True, "zwiftracing": True},
        }

        for key, value in blocks.items():
            current = doc.get(key)
            doc[key] = {**current, **value} if isinstance(current, dict) and isinstance(value, dict) else value

        services.store_profiles([doc])
        return RiderProfile.objects.get(zwid=zwid)

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
