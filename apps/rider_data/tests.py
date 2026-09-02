"""Guards on the cached rider profile.

The model is a cache of a remote document, and most of these tests exist to keep it one.
The failure mode being defended against is the one that produced ZRRider's 81 columns: a
field gets promoted because somebody needed the value, then another, until the cache is a
schema nobody can migrate.
"""

from datetime import timedelta

import pytest
from constance.test import override_config
from django.utils import timezone

from apps.rider_data.models import RiderProfile
from apps.rider_data.tasks import purge_rider_profiles
from gotta_bike_platform.retention import RetentionPolicy, policy_for


@pytest.fixture
def profile_factory(db):
    def _make(zwid=1001, *, last_race_days_ago=None, fetched_days_ago=0, requested_days_ago=None, **kwargs):
        return RiderProfile.objects.create(
            zwid=zwid,
            name=kwargs.pop("name", "Test Rider"),
            fetched_at=timezone.now() - timedelta(days=fetched_days_ago),
            last_requested_at=timezone.now()
            - timedelta(days=fetched_days_ago if requested_days_ago is None else requested_days_ago),
            last_race_at=(
                None if last_race_days_ago is None else timezone.now() - timedelta(days=last_race_days_ago)
            ),
            **kwargs,
        )

    return _make


@pytest.mark.django_db
def test_zwid_is_the_key_not_the_zwift_uuid(profile_factory):
    """Every consumer joins on User.zwid, and only connected riders have a UUID at all."""
    profile = profile_factory(zwid=12345)
    assert profile.pk == 12345
    assert RiderProfile._meta.pk.name == "zwid"

    # A rider with no zwift_user_id must still be storable.
    assert profile.zwift_user_id == ""


@pytest.mark.django_db
def test_the_promoted_columns_are_only_the_ones_something_queries():
    """The promotion rule, asserted rather than left as prose.

    A field earns a column by being filtered, sorted, joined or gated on. If this list grows,
    the growth should be a deliberate decision with a reason, not a drive-by addition -- which
    is exactly what did not happen to ZRRider.
    """
    columns = {f.name for f in RiderProfile._meta.get_fields()}
    expected = {
        "zwid", "zwift_user_id",
        "name", "gender", "country", "age",
        "weight_kg", "height_cm", "ftp", "zftp",
        "category_open", "category_women", "category_racing",
        "velo", "zwift_racing_score", "zp_skill", "compound_score",
        "phenotype_value", "club_id", "club_name",
        "payload", "fetched_at", "last_requested_at", "sources", "has_account", "last_race_at",
    }
    assert columns == expected, (
        "The column set changed. If a field was promoted out of payload, confirm something "
        "actually filters, sorts, joins or gates on it -- and update this test deliberately."
    )


@pytest.mark.django_db
def test_the_rich_blocks_stay_in_payload(profile_factory):
    """Power curves, handicaps, seed and phenotype scores are read whole, never queried."""
    profile = profile_factory(
        payload={
            "power": {"curve_w": {"5": 900}, "curve_wkg": {"5": 12.1}},
            "handicaps": {"flat": 1.2, "rolling": 0.9, "hilly": -0.4, "mountainous": -1.1},
            "seed": {"rating": 1600},
            "phenotype": {"scores": {"sprinter": 80}, "value": "Sprinter"},
        }
    )
    profile.refresh_from_db()
    assert profile.payload["handicaps"]["hilly"] == pytest.approx(-0.4)
    assert profile.payload["power"]["curve_w"]["5"] == 900
    # None of those are columns.
    assert not hasattr(profile, "handicap_hilly")
    assert not hasattr(profile, "curve_w")


@pytest.mark.django_db
def test_roster_membership_is_not_stored_here():
    """Membership belongs to team. A cache row carrying it could never be evicted."""
    columns = {f.name for f in RiderProfile._meta.get_fields()}
    for forbidden in ("date_left", "is_active", "on_team", "first_seen", "joined"):
        assert forbidden not in columns, (
            f"{forbidden} is roster state, not profile data — putting it on an evictable cache "
            f"makes retention unenforceable on exactly the rows that need it"
        )


@pytest.mark.django_db
def test_staleness_is_computed_not_stored(profile_factory):
    """A stored flag needs a sweep to stay true and is wrong between sweeps."""
    columns = {f.name for f in RiderProfile._meta.get_fields()}
    assert "is_stale" not in columns

    with override_config(RIDER_PROFILE_REFRESH_HOURS=24):
        assert profile_factory(zwid=1, fetched_days_ago=0).is_stale is False
        assert profile_factory(zwid=2, fetched_days_ago=3).is_stale is True


@pytest.mark.django_db
@override_config(RIDER_PROFILE_REFRESH_HOURS=0)
def test_zero_disables_staleness(profile_factory):
    """Matching the convention the analytics and verification sweeps already use."""
    assert profile_factory(fetched_days_ago=999).is_stale is False


# --- retention -----------------------------------------------------------------------


def test_the_model_declares_its_retention():
    """The ratchet requires it; this states what was decided."""
    policy = policy_for(RiderProfile)
    assert policy is not None
    assert policy.kind == RetentionPolicy.KIND_DELETE
    assert policy.anchor == "last_requested_at"
    assert policy.setting == "RIDER_PROFILE_MAX_DAYS"


@pytest.mark.django_db
def test_the_declared_window_is_the_configured_one():
    """The declaration names a Constance setting; this pins the default it documents."""
    from constance import config

    assert config.RIDER_PROFILE_MAX_DAYS == 120


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=120, RIDER_PROFILE_PURGE_MAX_FRACTION=0.9)
def test_a_rider_the_sync_has_stopped_asking_for_is_purged(profile_factory, healthy_sync):
    """Not asked for in the window means the rider left the set we have a reason to hold."""
    profile_factory(zwid=1, requested_days_ago=200)
    profile_factory(zwid=2, requested_days_ago=1)

    result = purge_rider_profiles.func()

    assert result["deleted"] == 1
    assert set(RiderProfile.objects.values_list("zwid", flat=True)) == {2}


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=120)
def test_race_activity_no_longer_decides_anything(profile_factory, healthy_sync):
    """A rider who has not raced in years stays, as long as the sync still refreshes them.

    This is the whole point of the anchor change: race activity describes the rider, not our
    reason for holding their data.
    """
    profile_factory(zwid=1, last_race_days_ago=2000, fetched_days_ago=0)

    purge_rider_profiles.func()

    assert RiderProfile.objects.filter(zwid=1).exists()


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=0)
def test_zero_disables_the_purge(profile_factory):
    profile_factory(zwid=1, last_race_days_ago=9999)

    result = purge_rider_profiles.func()

    assert result["deleted"] == 0
    assert RiderProfile.objects.filter(zwid=1).exists()


@pytest.mark.django_db
def test_the_purge_is_not_scheduled_yet():
    """Deliberate: nothing has validated last_race_at, so nothing should delete on it."""
    from gotta_bike_platform.task_registry import TASK_REGISTRY

    entry = TASK_REGISTRY["purge_rider_profiles"]
    assert not entry.get("scheduled", False), (
        "Scheduling this means deleting production rows on an anchor no sync has validated"
    )


# --- admin ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_cache_is_not_editable_in_admin():
    """Every field mirrors the service, so an edit would be overwritten by the next sync.

    A form that appears to save but silently reverts is worse than no form.
    """
    from django.contrib import admin

    model_admin = admin.site._registry[RiderProfile]
    readonly = set(model_admin.get_readonly_fields(None))
    editable = {f.name for f in RiderProfile._meta.fields} - readonly

    assert not editable, f"these would appear editable and silently revert: {sorted(editable)}"
    assert model_admin.has_add_permission(None) is False


@pytest.mark.django_db
@override_config(RIDER_PROFILE_MAX_DAYS=120)
def test_admin_shows_which_rows_eviction_would_reach(profile_factory):
    """The operational view of the policy: what is about to go, before it goes."""
    from apps.rider_data.admin import EvictionRiskFilter

    profile_factory(zwid=1, fetched_days_ago=1)
    profile_factory(zwid=2, fetched_days_ago=90)
    profile_factory(zwid=3, fetched_days_ago=200)

    def _filtered(value):
        filt = EvictionRiskFilter(None, {"refresh_status": [value]}, RiderProfile, None)
        return set(filt.queryset(None, RiderProfile.objects.all()).values_list("zwid", flat=True))

    assert _filtered("fresh") == {1}
    assert _filtered("aging") == {2}
    assert _filtered("evictable") == {3}
