"""Tests that a rider's Zwift verification is bound to the ZWID it verified.

The two self-service paths must keep ``zwid`` and the verification fields in step:

- ``/user/profile/manual-zwift-verify/`` used to write ``zwid`` alone, so an
  already-verified rider could repoint their account at any ZWID -- a teammate's
  included -- and stay verified, which is what the profile card and the roster
  identity rule join racing data on. Changing the ZWID must clear the verification
  in the same save.
- ``/user/profile/unverify-zwift/`` used to clear the flag but leave the provenance
  (``zwid_verification_method`` / ``zwid_verified_at``) describing a verification
  that no longer exists.

Both change ``zwid``, which selects the required verification types, so both must
also refresh the ``is_race_ready`` cache.
"""

import pytest
from django.urls import reverse

URL = reverse("accounts:manual_zwift_verify")


@pytest.fixture
def verified_rider(user_model):
    # A legacy-verified rider whose account already points at a ZWID.
    from django.utils import timezone

    return user_model.objects.create_user(
        username="verified-rider",
        zwid=111111,
        zwid_verified=True,
        zwid_verification_method="legacy",
        zwid_verified_at=timezone.now(),
    )


@pytest.mark.django_db
def test_changing_zwid_clears_verification(client, verified_rider):
    client.force_login(verified_rider)

    resp = client.post(URL, {"zwiftpower_url": "222222"})

    assert resp.status_code == 200
    verified_rider.refresh_from_db()
    assert verified_rider.zwid == 222222
    assert verified_rider.zwid_verified is False
    assert verified_rider.zwid_verification_method == ""
    assert verified_rider.zwid_verified_at is None
    assert verified_rider.has_accepted_zwid_verification is False


@pytest.mark.django_db
def test_changing_zwid_via_zwiftpower_url_clears_verification(client, verified_rider):
    client.force_login(verified_rider)

    client.post(URL, {"zwiftpower_url": "https://zwiftpower.com/profile.php?z=333333"})

    verified_rider.refresh_from_db()
    assert verified_rider.zwid == 333333
    assert verified_rider.zwid_verified is False


@pytest.mark.django_db
def test_resubmitting_the_same_zwid_keeps_verification(client, verified_rider):
    """Re-confirming the ZWID already on file is a no-op, not a self-inflicted unverify."""
    client.force_login(verified_rider)

    client.post(URL, {"zwiftpower_url": "111111"})

    verified_rider.refresh_from_db()
    assert verified_rider.zwid == 111111
    assert verified_rider.zwid_verified is True
    assert verified_rider.zwid_verification_method == "legacy"
    assert verified_rider.zwid_verified_at is not None


@pytest.mark.django_db
def test_invalid_input_leaves_zwid_and_verification_alone(client, verified_rider):
    client.force_login(verified_rider)

    resp = client.post(URL, {"zwiftpower_url": "not-a-zwid"})

    assert b"valid ZwiftPower profile URL" in resp.content
    verified_rider.refresh_from_db()
    assert verified_rider.zwid == 111111
    assert verified_rider.zwid_verified is True


@pytest.mark.django_db
def test_changing_zwid_refreshes_the_race_ready_cache(
    client, verified_rider, zp_team_rider_factory, verification_factory
):
    """Required types are keyed on ZWID -> ZP category, so the cached flag must be recomputed."""
    zp_team_rider_factory(zwid=111111, div=20)  # A-C: weight_full + height
    zp_team_rider_factory(zwid=222222, div=5)  # A+: weight_full + height + power
    verification_factory(verified_rider, "weight_full")
    verification_factory(verified_rider, "height")
    assert verified_rider.refresh_race_ready()[0] is True

    client.force_login(verified_rider)
    client.post(URL, {"zwiftpower_url": "222222"})

    verified_rider.refresh_from_db()
    assert verified_rider.is_race_ready is False  # A+ also needs a power verification


@pytest.mark.django_db
def test_removing_the_zwid_refreshes_the_race_ready_cache(
    client, verified_rider, zp_team_rider_factory, verification_factory
):
    """Dropping the ZWID falls back to the default requirements, which can move the status."""
    zp_team_rider_factory(zwid=111111, div=20)  # A-C: weight_full + height
    verification_factory(verified_rider, "weight_full")
    verification_factory(verified_rider, "height")
    assert verified_rider.refresh_race_ready()[0] is True

    client.force_login(verified_rider)
    client.post(reverse("accounts:unverify_zwift"))

    verified_rider.refresh_from_db()
    # No ZP category any more, so the default weight_light + height applies.
    assert verified_rider.zwid is None
    assert verified_rider.is_race_ready is False
