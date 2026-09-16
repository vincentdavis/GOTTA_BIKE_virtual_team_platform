"""Tests for a rider removing their own Zwift verification.

``/user/profile/unverify-zwift/`` used to clear the flag but leave the provenance
(``zwid_verification_method`` / ``zwid_verified_at``) describing a verification that no
longer exists, and it left the zauth link in place, so the hourly reconcile handed the
verification straight back to a connected rider.

It changes ``zwid``, which selects the required verification types, so it must also refresh
the ``is_race_ready`` cache -- and, being a deliberate rider action rather than a correction,
move the Discord role with it.

The manual ZWID form these tests once covered is gone: verification is zauth-only.
"""

from unittest.mock import patch

import pytest
from django.urls import NoReverseMatch, reverse

URL = reverse("accounts:unverify_zwift")


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


@pytest.fixture
def disconnects(monkeypatch):
    """Record every zauth disconnect and report a link as removed.

    Returns:
        The list the calls' user ids are appended to.

    """
    from apps.zwift.client import DisconnectOutcome

    calls = []
    monkeypatch.setattr("apps.zwift.client.disconnect_link", lambda uid: calls.append(uid) or DisconnectOutcome.REMOVED)
    return calls


def test_the_manual_zwid_form_is_gone():
    with pytest.raises(NoReverseMatch):
        reverse("accounts:manual_zwift_verify")


@pytest.mark.django_db
def test_removing_clears_the_whole_verification(client, verified_rider, disconnects):
    client.force_login(verified_rider)

    resp = client.post(URL)

    assert resp.status_code == 200
    verified_rider.refresh_from_db()
    assert verified_rider.zwid is None
    assert verified_rider.zwid_verified is False
    assert verified_rider.zwid_verification_method == ""
    assert verified_rider.zwid_verified_at is None


@pytest.mark.django_db
def test_removing_disconnects_the_zauth_link_so_it_sticks(client, user_model, disconnects):
    """Otherwise the hourly reconcile verifies a connected rider again."""
    from apps.zwift import verification

    rider = user_model.objects.create_user(
        username="zauth-rider", zwid=222222, zwid_verified=True, zwid_verification_method="zauth"
    )
    client.force_login(rider)

    client.post(URL)

    assert disconnects == [str(rider.pk)]
    # With the link gone the service lists no connection for them, and the reconcile agrees.
    with patch("apps.zwift.client.list_connections", return_value=[]):
        verification.reconcile_all()
    rider.refresh_from_db()
    assert rider.zwid_verified is False


# The refusal when the service cannot confirm the link is gone is covered in
# test_zwift_remove_honesty.py.


@pytest.mark.django_db
def test_the_swap_keeps_the_zauth_block(client, verified_rider, disconnects, monkeypatch):
    """The HTMX re-render gets the same context as the profile page, not just the user.

    It used to render with ``{"user": ...}`` alone, so the Zwift Official Auth line vanished
    from the page until a reload.
    """
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.get_connection_status",
        lambda uid: {"connected": False, "zwid": None, "connected_at": None},
    )
    client.force_login(verified_rider)

    body = client.post(URL, HTTP_HX_REQUEST="true").content.decode()

    assert "Zwift Account Not Verified" in body
    assert "Zwift Official Auth" in body
    assert reverse("zwift:zauth") in body


@pytest.mark.django_db
def test_the_edit_page_shows_the_connection_but_not_the_rating_cards(
    client, verified_rider, zp_team_rider_factory, monkeypatch
):
    """The edit page gets the Zwift Link block the swap renders, but never showed ZP/ZR cards.

    Its "Your Racing Data" card already covers them, so a second copy would be noise.
    """
    zp_team_rider_factory(zwid=verified_rider.zwid)
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.get_connection_status",
        lambda uid: {"connected": False, "zwid": None, "connected_at": None},
    )
    client.force_login(verified_rider)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert "Zwift Account Verified" in body
    assert "Zwift Official Auth" in body
    assert "Not found in Zwift Racing" not in body
    assert reverse("accounts:refresh_zr") not in body


@pytest.mark.django_db
def test_removing_the_zwid_refreshes_the_race_ready_cache(
    client, verified_rider, zp_team_rider_factory, verification_factory, disconnects
):
    """Dropping the ZWID falls back to the default requirements, which can move the status."""
    zp_team_rider_factory(zwid=111111, div=20)  # A-C: weight_full + height
    verification_factory(verified_rider, "weight_full")
    verification_factory(verified_rider, "height")
    assert verified_rider.refresh_race_ready()[0] is True

    client.force_login(verified_rider)
    client.post(URL)

    verified_rider.refresh_from_db()
    # No ZP category any more, so the default weight_light + height applies.
    assert verified_rider.zwid is None
    assert verified_rider.is_race_ready is False


@pytest.mark.django_db
def test_losing_the_status_moves_the_discord_role_immediately(
    client, verified_rider, zp_team_rider_factory, verification_factory, disconnects
):
    """Otherwise the rider keeps the race-ready role until the 6-hourly sweep notices."""
    zp_team_rider_factory(zwid=111111, div=20)  # A-C: weight_full + height
    verification_factory(verified_rider, "weight_full")
    verification_factory(verified_rider, "height")
    verified_rider.refresh_race_ready()
    client.force_login(verified_rider)

    with patch("apps.team.tasks.notify_race_ready_change") as task:
        client.post(URL)

    task.enqueue.assert_called_once()
    assert task.enqueue.call_args[1]["is_now_race_ready"] is False


@pytest.mark.django_db
def test_gaining_the_status_moves_the_role_too(
    client, verified_rider, zp_team_rider_factory, verification_factory, disconnects
):
    """Dropping the ZWID drops the ZP category, which can leave a rider meeting the default."""
    zp_team_rider_factory(zwid=111111, div=10)  # A-C: weight_full + height, which they lack
    verification_factory(verified_rider, "weight_light")
    verification_factory(verified_rider, "height")
    assert verified_rider.refresh_race_ready()[0] is False
    client.force_login(verified_rider)

    with patch("apps.team.tasks.notify_race_ready_change") as task:
        client.post(URL)

    verified_rider.refresh_from_db()
    assert verified_rider.is_race_ready is True  # default weight_light + height
    task.enqueue.assert_called_once()
    assert task.enqueue.call_args[1]["is_now_race_ready"] is True


@pytest.mark.django_db
def test_no_role_churn_when_the_status_did_not_change(client, verified_rider, disconnects):
    """A rider who was never race verified should not trigger a Discord write."""
    client.force_login(verified_rider)

    with patch("apps.team.tasks.notify_race_ready_change") as task:
        client.post(URL)

    task.enqueue.assert_not_called()


@pytest.mark.django_db
def test_the_dropped_zwid_and_the_link_removal_are_logged(client, verified_rider, disconnects):
    client.force_login(verified_rider)

    with patch("apps.accounts.views.logfire") as fake_logfire:
        client.post(URL)

    kwargs = fake_logfire.info.call_args[1]
    assert kwargs["old_zwid"] == 111111
    assert kwargs["verified_by_zwift_link"] is False
    assert kwargs["zwift_link_removed"] is True
    assert kwargs["zwift_link_outcome"] == "removed"
    # Logfire scrubs any attribute whose name contains "auth" in production.
    assert not [name for name in kwargs if "auth" in name]
