"""A rider's "Remove" / "Disconnect Zwift" only reports what the Zwift service confirmed.

The profile's Remove used to clear the verification even when the disconnect call failed,
so the rider saw "Not Verified" while the link survived and the hourly reconcile verified
them again. The connection page told a rider whose call failed that no connected account
was found. Both now refuse, or say so, unless the service confirmed there is no link.
Service calls are patched at the ``apps.zwift.client`` boundary.
"""

from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.zwift import verification
from apps.zwift.client import DisconnectOutcome

REMOVE_URL = reverse("accounts:unverify_zwift")
REFUSED = "couldn&#x27;t reach Zwift to disconnect your account"
REMOVED_STATUS = '<p class="text-sm mb-2" role="status">Zwift verification removed.</p>'


@pytest.fixture(autouse=True)
def _clear_cache():
    """Start without a failed status read remembered by another test."""
    cache.clear()
    yield
    cache.clear()


def _rider(user_model, method="zauth"):
    return user_model.objects.create_user(
        username=f"{method}-rider",
        zwid=222222,
        zwid_verified=True,
        zwid_verification_method=method,
        zwid_verified_at=timezone.now(),
    )


@pytest.fixture
def service(monkeypatch):
    """Stand in for the zauth service.

    Returns:
        A dict: set ``disconnect`` (a DisconnectOutcome) and ``status`` to shape the answers.

    """
    state = {"disconnect": DisconnectOutcome.REMOVED, "status": {"connected": False}, "calls": []}

    def disconnect_link(user_id):
        state["calls"].append(("disconnect", user_id))
        return state["disconnect"]

    def status(user_id):
        state["calls"].append(("status", user_id))
        return state["status"]

    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.disconnect_link", disconnect_link)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", status)
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)
    return state


def _assert_still_verified(rider, method="zauth"):
    rider.refresh_from_db()
    assert rider.zwid == 222222
    assert rider.zwid_verified is True
    assert rider.zwid_verification_method == method
    assert rider.zwid_verified_at is not None


# --- the profile's "Remove" ---------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["zauth", "legacy"])
def test_remove_is_refused_when_the_service_cannot_be_reached(client, user_model, service, method):
    rider = _rider(user_model, method)
    service["disconnect"] = DisconnectOutcome.FAILED
    service["status"] = None
    client.force_login(rider)

    body = client.post(REMOVE_URL, HTTP_HX_REQUEST="true").content.decode()

    _assert_still_verified(rider, method)
    assert 'role="alert"' in body
    assert REFUSED in body
    assert REMOVED_STATUS not in body
    assert "Zwift Account Verified" in body
    # The link block says it could not check, rather than "Not connected" next to a Connect button.
    assert "Couldn't check" in body
    assert "Not connected" not in body


@pytest.mark.django_db
def test_the_reconcile_after_a_refusal_matches_what_the_rider_was_shown(client, user_model, service):
    """The probe case: the link survived, so the rider must still be shown as verified."""
    rider = _rider(user_model)
    service["disconnect"] = DisconnectOutcome.FAILED
    client.force_login(rider)
    client.post(REMOVE_URL)

    with patch("apps.zwift.client.list_connections", return_value=[{"user_id": str(rider.pk), "zwid": "222222"}]):
        verification.reconcile_all()

    _assert_still_verified(rider)


@pytest.mark.django_db
def test_remove_is_refused_for_a_link_verification_when_the_service_is_not_configured(client, user_model, service):
    rider = _rider(user_model, "zauth")
    service["disconnect"] = DisconnectOutcome.UNCONFIGURED
    client.force_login(rider)

    body = client.post(REMOVE_URL).content.decode()

    _assert_still_verified(rider)
    assert REFUSED in body


@pytest.mark.django_db
def test_a_legacy_verification_is_removed_when_the_service_is_not_configured(client, user_model, service):
    """No service here means no link to leave behind for a verification that never used one."""
    rider = _rider(user_model, "legacy")
    service["disconnect"] = DisconnectOutcome.UNCONFIGURED
    client.force_login(rider)

    body = client.post(REMOVE_URL).content.decode()

    rider.refresh_from_db()
    assert rider.zwid_verified is False
    assert REMOVED_STATUS in body


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", [DisconnectOutcome.REMOVED, DisconnectOutcome.NO_LINK])
def test_a_confirmed_removal_clears_it_and_says_so(client, user_model, service, outcome):
    rider = _rider(user_model)
    service["disconnect"] = outcome
    client.force_login(rider)

    body = client.post(REMOVE_URL, HTTP_HX_REQUEST="true").content.decode()

    rider.refresh_from_db()
    assert rider.zwid is None
    assert rider.zwid_verified is False
    assert rider.zwid_verification_method == ""
    assert REMOVED_STATUS in body
    assert 'role="alert"' not in body
    assert "Zwift Account Not Verified" in body


@pytest.mark.django_db
def test_a_refusal_is_logged_with_ids_and_nothing_scrubbable(client, user_model, service):
    rider = _rider(user_model)
    service["disconnect"] = DisconnectOutcome.FAILED
    client.force_login(rider)

    with patch("apps.accounts.views.logfire") as fake_logfire:
        client.post(REMOVE_URL)

    kwargs = fake_logfire.warning.call_args.kwargs
    assert kwargs == {"user_id": rider.pk, "zwift_link_outcome": "failed", "verified_by_zwift_link": True}
    fake_logfire.info.assert_not_called()


@pytest.mark.django_db
def test_the_removal_log_carries_no_value_production_would_scrub(client, user_model, service):
    """Production Logfire scrubs any attribute whose name or string value contains "auth"."""
    rider = _rider(user_model, "zauth")
    client.force_login(rider)

    with patch("apps.accounts.views.logfire") as fake_logfire:
        client.post(REMOVE_URL)

    kwargs = fake_logfire.info.call_args.kwargs
    assert kwargs["verified_by_zwift_link"] is True
    assert kwargs["zwift_link_outcome"] == "removed"
    assert not [name for name in kwargs if "auth" in name]
    assert not [value for value in kwargs.values() if isinstance(value, str) and "auth" in value]


@pytest.mark.django_db
def test_a_refused_remove_on_the_edit_page_keeps_the_rating_cards_off(
    client, user_model, service, zp_team_rider_factory
):
    """The edit page never shows the ZP/ZR cards, so its swap must not add them."""
    rider = _rider(user_model)
    zp_team_rider_factory(zwid=rider.zwid)
    service["disconnect"] = DisconnectOutcome.FAILED
    client.force_login(rider)
    edit_url = "http://testserver" + reverse("accounts:profile_edit")
    profile_url = "http://testserver" + reverse("accounts:profile")

    on_edit = client.post(REMOVE_URL, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL=edit_url).content.decode()
    on_profile = client.post(REMOVE_URL, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL=profile_url).content.decode()

    assert "Zwift Account Verified" in on_edit
    assert "ZwiftPower" not in on_edit
    assert "ZwiftPower" in on_profile


# --- the Zwift Link block's unknown state -------------------------------------------------


@pytest.mark.django_db
def test_the_link_block_does_not_claim_not_connected_when_it_could_not_check(client, user_model, service):
    rider = _rider(user_model)
    service["status"] = None
    client.force_login(rider)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert "Couldn't check" in body
    assert "reach the Zwift service to check your connection" in body
    assert "· Not connected" not in body


@pytest.mark.django_db
def test_the_link_block_still_offers_connect_when_the_service_says_not_connected(client, user_model, service):
    rider = _rider(user_model, "legacy")
    client.force_login(rider)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert "· Not connected" in body
    assert "Couldn't check" not in body


# --- the connection page's "Disconnect Zwift" ---------------------------------------------


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.mark.django_db
def test_a_failed_disconnect_says_so_and_changes_nothing(client, user_model, service):
    rider = _rider(user_model)
    service["disconnect"] = DisconnectOutcome.FAILED
    client.force_login(rider)

    response = client.post(reverse("zwift:zauth_disconnect"))

    shown = _messages(response)
    assert any("couldn't reach Zwift to disconnect" in m for m in shown)
    assert not any("No connected Zwift account was found" in m for m in shown)
    _assert_still_verified(rider)


@pytest.mark.django_db
def test_a_disconnect_without_a_configured_service_says_so(client, user_model, service):
    rider = _rider(user_model)
    service["disconnect"] = DisconnectOutcome.UNCONFIGURED
    client.force_login(rider)

    shown = _messages(client.post(reverse("zwift:zauth_disconnect")))

    assert any("isn't configured" in m for m in shown)
    assert not any("No connected Zwift account was found" in m for m in shown)
    _assert_still_verified(rider)


@pytest.mark.django_db
def test_no_link_is_reported_as_no_link(client, user_model, service):
    rider = _rider(user_model, "legacy")
    service["disconnect"] = DisconnectOutcome.NO_LINK
    client.force_login(rider)

    shown = _messages(client.post(reverse("zwift:zauth_disconnect")))

    assert any("No connected Zwift account was found" in m for m in shown)
    _assert_still_verified(rider, "legacy")  # only a link verification ends with the link


@pytest.mark.django_db
def test_a_confirmed_disconnect_ends_the_verification_even_if_the_next_read_fails(client, user_model, service):
    rider = _rider(user_model)
    service["disconnect"] = DisconnectOutcome.REMOVED
    service["status"] = None  # the page the redirect lands on cannot read the status
    client.force_login(rider)

    with patch("apps.zwift.views.logfire") as fake_logfire:
        response = client.post(reverse("zwift:zauth_disconnect"), follow=True)

    rider.refresh_from_db()
    assert rider.zwid_verified is False
    assert rider.zwid_verification_method == ""
    assert any("was disconnected" in str(m) for m in response.context["messages"])
    logged = [call.kwargs for call in fake_logfire.info.call_args_list if "outcome" in call.kwargs]
    assert logged == [{"user_id": rider.pk, "outcome": "removed"}]
