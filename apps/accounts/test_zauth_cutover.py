"""Phase 5: what ZAUTH_VERIFICATION_REQUIRED changes when it is turned on.

The flag reinterprets an existing verification rather than deleting it, so every
test here also pins that turning it back off restores the previous behaviour.
"""

import pytest
from constance.test import override_config
from django.urls import NoReverseMatch, reverse

from apps.team.models import RaceReadyRecord
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
def test_the_verification_report_still_uses_the_raw_column(client, user_model, monkeypatch):
    """The report exists to count the legacy backlog the flag stops accepting.

    Routing it through the policy would file every legacy member under "not verified" and
    hide the very number an admin watches before and after switching the flag on.
    """
    _user(user_model, "legacy")
    admin = user_model.objects.create_user(username="report-admin", permission_overrides={"membership_admin": True})
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: False)
    client.force_login(admin)

    counts = client.get(reverse("team:zwift_connections")).context["counts"]

    assert counts["legacy"] == 1
    assert counts["unverified"] == 0


def test_the_reviewer_zwid_queue_is_gone():
    """Verification is zauth-only: there is no queue of typed ZWIDs for staff to approve."""
    for name, args in (
        ("team:zwid_verification_action", [1]),
        ("team:application_zwid_admin_action", ["00000000-0000-0000-0000-000000000000"]),
        ("team:application_manual_zwift_verify", ["00000000-0000-0000-0000-000000000000"]),
    ):
        with pytest.raises(NoReverseMatch):
            reverse(name, args=args)


@pytest.mark.django_db
def test_the_reviewer_page_no_longer_lists_typed_zwids(client, user_model, superuser):
    user_model.objects.create_user(username="typed", discord_username="typed-zwid-rider", zwid=4242)
    client.force_login(superuser)

    body = client.get(reverse("team:verification_records")).content.decode()

    assert "Pending ZWID" not in body
    assert "typed-zwid-rider" not in body


# --- rider pages under the flag -------------------------------------------------


@pytest.fixture
def legacy_rider(user_model):
    """Build a legacy-verified team member.

    Returns:
        The rider.

    """
    rider = _user(user_model, "legacy")
    rider.permission_overrides = {"team_member": True}
    rider.save(update_fields=["permission_overrides"])
    return rider


def _submit_height(client):
    return client.post(
        reverse("accounts:submit_race_ready"),
        {
            "verify_type": "height",
            "media_type": "link",
            "url": "https://example.test/evidence",
            "record_date": "2026-09-01",
            "height": "178",
        },
    )


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_a_legacy_rider_cannot_submit_records_once_required(client, legacy_rider):
    """The page hides the form; the view must refuse too, or a stale tab still gets through."""
    client.force_login(legacy_rider)

    resp = _submit_height(client)

    assert resp.status_code == 302
    assert resp["Location"] == reverse("accounts:verification")
    assert not RaceReadyRecord.objects.filter(user=legacy_rider).exists()


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_an_htmx_submission_is_sent_back_to_the_page(client, legacy_rider):
    client.force_login(legacy_rider)

    resp = client.post(reverse("accounts:submit_race_ready"), {"verify_type": "height"}, HTTP_HX_REQUEST="true")

    assert resp["HX-Redirect"] == reverse("accounts:verification")
    assert not RaceReadyRecord.objects.filter(user=legacy_rider).exists()


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=False)
def test_the_same_rider_can_submit_while_the_flag_is_off(client, legacy_rider):
    client.force_login(legacy_rider)

    _submit_height(client)

    assert RaceReadyRecord.objects.filter(user=legacy_rider, verify_type="height").exists()


@pytest.mark.django_db
def test_an_unverified_rider_cannot_submit_records(client, user_model):
    rider = _user(user_model, "", verified=False)
    client.force_login(rider)

    _submit_height(client)

    assert not RaceReadyRecord.objects.filter(user=rider).exists()


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_the_verification_page_asks_a_legacy_rider_to_connect(client, legacy_rider):
    client.force_login(legacy_rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert f'href="{reverse("zwift:zauth")}"' in body
    assert "Can't connect? Ask a team admin in Discord." in body
    assert 'id="race-ready-form-container"' not in body


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=False)
def test_the_verification_page_shows_the_form_while_the_flag_is_off(client, legacy_rider):
    client.force_login(legacy_rider)

    body = client.get(reverse("accounts:verification")).content.decode()

    assert 'id="race-ready-form-container"' in body
    assert "Ask a team admin in Discord" not in body


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=True)
def test_the_public_profile_reads_the_policy(client, legacy_rider, team_member):
    client.force_login(team_member)

    body = client.get(reverse("accounts:public_profile", args=[legacy_rider.pk])).content.decode()

    # The Zwift Status card's "Zwift Verified" row, and no source links for an unaccepted zwid.
    assert "zwiftpower.com/profile.php?z=1234" not in body
    row = body.split("Zwift Verified", 1)[1][:200]
    assert ">No<" in row


@pytest.mark.django_db
@override_config(ZAUTH_VERIFICATION_REQUIRED=False)
def test_the_public_profile_is_unchanged_while_the_flag_is_off(client, legacy_rider, team_member):
    client.force_login(team_member)

    body = client.get(reverse("accounts:public_profile", args=[legacy_rider.pk])).content.decode()

    assert "zwiftpower.com/profile.php?z=1234" in body
    row = body.split("Zwift Verified", 1)[1][:200]
    assert ">Yes<" in row
