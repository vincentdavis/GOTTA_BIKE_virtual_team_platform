"""Phase 5: what ZAUTH_VERIFICATION_REQUIRED changes when it is turned on.

The flag reinterprets an existing verification rather than deleting it, so every
test here also pins that turning it back off restores the previous behaviour.
"""

import pytest
from constance.test import override_config

from apps.team.services import verification_accepted


def _user(user_model, method, *, verified=True):
    return user_model.objects.create_user(
        username=f"u-{method or 'none'}",
        discord_id=f"d-{method or 'none'}",
        first_name="Test",
        last_name="Rider",
        zwid=1234,
        zwid_verified=verified,
        zwid_verification_method=method,
    )


# --- the property -------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["zauth", "legacy", "admin"])
@override_config(ZAUTH_VERIFICATION_REQUIRED=False)
def test_every_method_is_accepted_while_the_flag_is_off(user_model, method):
    assert _user(user_model, method).has_accepted_zwid_verification is True


@pytest.mark.django_db
@pytest.mark.parametrize(("method", "accepted"), [("zauth", True), ("legacy", False), ("admin", False)])
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_only_zauth_is_accepted_once_the_flag_is_on(user_model, method, accepted):
    assert _user(user_model, method).has_accepted_zwid_verification is accepted


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_an_unverified_user_stays_unverified(user_model):
    assert _user(user_model, "", verified=False).has_accepted_zwid_verification is False


@pytest.mark.django_db
def test_the_stored_record_is_never_mutated(user_model):
    """The flag reinterprets; it must not rewrite, or the grandfather is lost."""
    user = _user(user_model, "legacy")

    with override_config(ZAUTH_VERIFICATION_REQUIRED=True):
        assert user.has_accepted_zwid_verification is False

    user.refresh_from_db()
    assert user.zwid_verified is True
    assert user.zwid_verification_method == "legacy"
    with override_config(ZAUTH_VERIFICATION_REQUIRED=False):
        assert user.has_accepted_zwid_verification is True


# --- profile completion -------------------------------------------------------


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_a_legacy_user_reads_as_incomplete_once_required(user_model):
    user = _user(user_model, "legacy")
    user.birth_year = 1990
    user.gender = "male"
    user.timezone = "UTC"
    user.country = "US"
    user.trainer = "Wahoo KICKR"
    user.heartrate_monitor = "Wahoo TICKR"
    user.save()

    assert user.is_profile_complete is False
    # The banner lists missing fields from this dict, so it has to agree.
    assert user.profile_completion_status["zwid_verified"] is False


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=False)
def test_the_same_user_is_complete_while_the_flag_is_off(user_model):
    user = _user(user_model, "legacy")
    user.birth_year = 1990
    user.gender = "male"
    user.timezone = "UTC"
    user.country = "US"
    user.trainer = "Wahoo KICKR"
    user.heartrate_monitor = "Wahoo TICKR"
    user.save()

    assert user.is_profile_complete is True
    assert user.profile_completion_status["zwid_verified"] is True


# --- roster rows (.values() dicts, where the property cannot reach) -----------


@pytest.mark.django_db  # constance reads its values from the database
@pytest.mark.parametrize(
    ("flag", "method", "expected"),
    [
        (False, "legacy", True),
        (False, "zauth", True),
        (True, "legacy", False),
        (True, "admin", False),
        (True, "zauth", True),
    ],
)
def test_verification_accepted_mirrors_the_property(flag, method, expected):
    row = {"zwid_verified": True, "zwid_verification_method": method}
    with override_config(ZAUTH_VERIFICATION_REQUIRED=flag):
        assert verification_accepted(row) is expected


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_verification_accepted_handles_an_unverified_row():
    assert verification_accepted({"zwid_verified": False, "zwid_verification_method": "zauth"}) is False


# --- what the flag must NOT touch ---------------------------------------------


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_race_ready_is_unaffected(user_model, verification_factory, zp_team_rider_factory):
    """calculate_race_ready reads verification records, never zwid_verified."""
    zp_team_rider_factory(zwid=1234, div=40, divw=40)
    user = _user(user_model, "legacy")
    verification_factory(user, "weight_light")
    verification_factory(user, "height")

    assert user.calculate_race_ready() is True


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_the_admin_review_queue_still_uses_the_raw_column(user_model):
    """That queue is for pending review requests, not the migration backlog.

    Routing it through the policy would drop every legacy member into it at once.
    """
    _user(user_model, "legacy")

    pending = user_model.objects.filter(zwid__isnull=False, zwid_verified=False)

    assert pending.count() == 0
