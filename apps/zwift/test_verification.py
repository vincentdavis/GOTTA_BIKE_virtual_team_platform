"""Tests for zauth verification reconcile (phase 2), incl. the two safety guards.

Service calls are patched at the ``apps.zwift.client`` boundary.
"""

import pytest

from apps.zwift import verification


@pytest.mark.django_db
def test_zauth_literal_matches_enum():
    from apps.accounts.models import User

    assert verification.ZAUTH == User.VerificationMethod.ZAUTH


# --- apply_status (single user) ---------------------------------------------


@pytest.mark.django_db
def test_apply_status_connected_grants(user):
    outcome = verification.apply_status(
        user, {"connected": True, "zwid": "555", "zwift_user_id": "uuid-1", "connected_at": None}
    )
    user.refresh_from_db()
    assert outcome == "granted"
    assert user.zwid == 555
    assert user.zwid_verified is True
    assert user.zwid_verification_method == "zauth"
    assert user.zwid_verified_at is not None


@pytest.mark.django_db
def test_apply_status_connected_is_idempotent(user):
    status = {"connected": True, "zwid": "555"}
    assert verification.apply_status(user, status) == "granted"
    assert verification.apply_status(user, status) == "unchanged"


@pytest.mark.django_db
def test_apply_status_disconnect_revokes_zauth_but_keeps_zwid(user_model):
    u = user_model.objects.create_user(username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth")
    outcome = verification.apply_status(u, {"connected": False})
    u.refresh_from_db()
    assert outcome == "revoked"
    assert u.zwid_verified is False
    assert u.zwid_verification_method == ""
    assert u.zwid == 555  # last-known zwid kept


@pytest.mark.django_db
def test_apply_status_disconnect_leaves_legacy_untouched(user_model):
    """Guard 1: a not-connected legacy user must not be revoked."""
    u = user_model.objects.create_user(username="leg", zwid=777, zwid_verified=True, zwid_verification_method="legacy")
    outcome = verification.apply_status(u, {"connected": False})
    u.refresh_from_db()
    assert outcome == "unchanged"
    assert u.zwid_verified is True
    assert u.zwid_verification_method == "legacy"


@pytest.mark.django_db
def test_apply_status_none_skips(user_model):
    """Guard 2: a None status (service unavailable) never revokes."""
    u = user_model.objects.create_user(username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth")
    outcome = verification.apply_status(u, None)
    u.refresh_from_db()
    assert outcome == "skipped"
    assert u.zwid_verified is True
    assert u.zwid_verification_method == "zauth"


# --- zwid validation on the grant path --------------------------------------


@pytest.mark.parametrize("bad_zwid", [None, "", "  ", "abc", "0", "-5", "99999999999", "12.5"])
@pytest.mark.django_db
def test_apply_status_refuses_to_verify_without_a_usable_zwid(user, bad_zwid):
    """Connected but no storable official zwid -> no verification, no write."""
    outcome = verification.apply_status(user, {"connected": True, "zwid": bad_zwid})
    user.refresh_from_db()
    assert outcome == "invalid_zwid"
    assert user.zwid is None
    assert user.zwid_verified is False
    assert user.zwid_verification_method == ""


@pytest.mark.django_db
def test_apply_status_does_not_overstamp_a_self_reported_zwid(user_model):
    """A legacy zwid must never be relabelled 'zauth' without an official zwid."""
    u = user_model.objects.create_user(username="leg", zwid=777, zwid_verified=True, zwid_verification_method="legacy")
    outcome = verification.apply_status(u, {"connected": True, "zwid": None})
    u.refresh_from_db()
    assert outcome == "invalid_zwid"
    assert u.zwid == 777
    assert u.zwid_verification_method == "legacy"


@pytest.mark.django_db
def test_grant_refusal_never_revokes_an_existing_zauth_user(user_model):
    """A missing zwid on the grant path is not a disconnect: verification stands."""
    u = user_model.objects.create_user(username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth")
    outcome = verification.apply_status(u, {"connected": True, "zwid": None})
    u.refresh_from_db()
    assert outcome == "invalid_zwid"
    assert u.zwid_verified is True
    assert u.zwid_verification_method == "zauth"
    assert u.zwid == 555


@pytest.mark.django_db
def test_grant_refreshes_cached_race_ready_when_zwid_changes(user_model, zp_team_rider_factory, verification_factory):
    """Adopting the official zwid must not leave the is_race_ready cache stale."""
    u = user_model.objects.create_user(username="nz", zwid=None)
    verification_factory(u, "weight_light")
    verification_factory(u, "height")
    u.refresh_race_ready()
    assert u.is_race_ready is True  # DEFAULT types are satisfied while zwid is unset

    # The official zwid maps to an A+ rider, who also needs weight_full + power.
    zp_team_rider_factory(zwid=555, div=5, divw=5)

    assert verification.apply_status(u, {"connected": True, "zwid": "555"}) == "granted"

    u.refresh_from_db()
    assert u.zwid == 555
    assert u.is_race_ready is False  # recomputed, not left stale


@pytest.mark.django_db
def test_grant_leaves_race_ready_alone_when_zwid_is_unchanged(user_model, zp_team_rider_factory, verification_factory):
    """The common case: zwid already matches, so nothing downstream is recomputed."""
    zp_team_rider_factory(zwid=555, div=5, divw=5)
    u = user_model.objects.create_user(username="same", zwid=555)
    verification_factory(u, "weight_full")
    verification_factory(u, "height")
    verification_factory(u, "power")
    u.refresh_race_ready()
    assert u.is_race_ready is True

    assert verification.apply_status(u, {"connected": True, "zwid": "555"}) == "granted"

    u.refresh_from_db()
    assert u.zwid == 555
    assert u.is_race_ready is True


@pytest.mark.django_db
def test_apply_status_accepts_the_largest_storable_zwid(user):
    outcome = verification.apply_status(user, {"connected": True, "zwid": "2147483647"})
    user.refresh_from_db()
    assert outcome == "granted"
    assert user.zwid == 2147483647


# --- reconcile_all (bulk task) ----------------------------------------------


@pytest.mark.django_db
def test_reconcile_skips_on_service_unavailable(user_model, monkeypatch):
    """Guard 2: None from list_connections aborts with no changes."""
    u = user_model.objects.create_user(username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth")
    monkeypatch.setattr("apps.zwift.client.list_connections", lambda: None)

    result = verification.reconcile_all()

    u.refresh_from_db()
    assert result["status"] == "skipped"
    assert u.zwid_verified is True  # untouched


@pytest.mark.django_db
def test_reconcile_grants_revokes_and_spares_legacy(user_model, monkeypatch):
    connected_user = user_model.objects.create_user(username="c", zwid=None)
    stale_zauth = user_model.objects.create_user(
        username="s", zwid=999, zwid_verified=True, zwid_verification_method="zauth"
    )
    legacy = user_model.objects.create_user(
        username="l", zwid=111, zwid_verified=True, zwid_verification_method="legacy"
    )

    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [{"user_id": str(connected_user.pk), "zwid": "555"}],
    )

    result = verification.reconcile_all()

    connected_user.refresh_from_db()
    stale_zauth.refresh_from_db()
    legacy.refresh_from_db()

    assert result == {"status": "completed", "connected": 1, "granted": 1, "revoked": 1, "invalid_zwid": 0}
    # connected -> granted
    assert connected_user.zwid == 555
    assert connected_user.zwid_verified is True
    assert connected_user.zwid_verification_method == "zauth"
    # zauth not in list -> revoked, zwid kept
    assert stale_zauth.zwid_verified is False
    assert stale_zauth.zwid_verification_method == ""
    assert stale_zauth.zwid == 999
    # legacy not in list -> untouched (guard 1)
    assert legacy.zwid_verified is True
    assert legacy.zwid_verification_method == "legacy"


@pytest.mark.django_db
def test_reconcile_empty_list_revokes_only_zauth(user_model, monkeypatch):
    """A genuine empty list (service up, nobody connected) revokes zauth, spares legacy."""
    zauth_user = user_model.objects.create_user(
        username="z", zwid=555, zwid_verified=True, zwid_verification_method="zauth"
    )
    legacy = user_model.objects.create_user(
        username="l", zwid=111, zwid_verified=True, zwid_verification_method="legacy"
    )
    monkeypatch.setattr("apps.zwift.client.list_connections", list)

    result = verification.reconcile_all()

    zauth_user.refresh_from_db()
    legacy.refresh_from_db()
    assert result["revoked"] == 1
    assert zauth_user.zwid_verified is False
    assert legacy.zwid_verified is True


@pytest.mark.django_db
def test_reconcile_bad_zwid_does_not_abort_the_revoke_pass(user_model, monkeypatch):
    """An unstorable zwid is counted and skipped, not allowed to kill the run."""
    bad = user_model.objects.create_user(username="b", zwid=None)
    stale_zauth = user_model.objects.create_user(
        username="s", zwid=999, zwid_verified=True, zwid_verification_method="zauth"
    )
    monkeypatch.setattr(
        "apps.zwift.client.list_connections",
        lambda: [{"user_id": str(bad.pk), "zwid": "99999999999"}],
    )

    result = verification.reconcile_all()

    bad.refresh_from_db()
    stale_zauth.refresh_from_db()
    assert result["invalid_zwid"] == 1
    assert result["granted"] == 0
    assert result["revoked"] == 1  # the revoke pass still ran
    assert bad.zwid_verified is False
    assert stale_zauth.zwid_verified is False
