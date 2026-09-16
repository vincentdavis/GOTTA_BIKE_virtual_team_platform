"""A registration's Zwift link is dropped before the registration is deleted.

The zauth service keys that link by the registration's UUID and knows nothing about this
table, so a deleted row would otherwise leave a Zwift account tied to the person in the
service with nothing left that could reach it. Every delete path is covered: the single and
bulk views, both Django admin paths, and account deletion.

Most tests stand in for the service at the ``apps.zwift.client`` boundary. The outage tests
run the real client with ``httpx`` patched instead, because the failure being guarded against
is the client swallowing an HTTP error -- a fake that raises from the client function itself
would pass without exercising it.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.contrib.admin.sites import site
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.services import delete_user_account
from apps.team.models import MembershipApplication
from apps.team.services import ZWIFT_LINK_NOT_RELEASED_MESSAGE, release_application_zwift_links
from apps.zwift.client import DisconnectOutcome
from gotta_bike_platform.config import settings as config

SERVICE = "http://svc.internal:8000"


@pytest.fixture
def service(monkeypatch):
    """Stand in for the zauth service.

    Returns:
        A dict: put ids in ``linked`` for the service to hold links for; read
        ``disconnects`` for the ids it was asked to drop and ``list_calls`` for how often
        its connections list was read.

    """
    state = {"linked": set(), "disconnects": [], "list_calls": 0}

    def list_connections():
        state["list_calls"] += 1
        return [{"user_id": uid, "zwid": "4242"} for uid in sorted(state["linked"])]

    def disconnect_link(uid):
        state["disconnects"].append(uid)
        if uid in state["linked"]:
            state["linked"].discard(uid)
            return DisconnectOutcome.REMOVED
        return DisconnectOutcome.NO_LINK

    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.list_connections", list_connections)
    monkeypatch.setattr("apps.zwift.client.disconnect_link", disconnect_link)
    return state


@pytest.fixture
def configured(monkeypatch):
    """Configure the real client against a service address nothing listens on."""
    monkeypatch.setattr(config, "zwift_api_base_url", SERVICE)
    monkeypatch.setattr(config, "zwift_app_api_key", "app-key-123")


def _response(method: str, path: str, status_code: int, body=None) -> httpx.Response:
    """Build a response the way httpx would hand it back.

    Returns:
        The response, with a JSON body when one is given.

    """
    request = httpx.Request(method, f"{SERVICE}{path}")
    if body is None:
        return httpx.Response(status_code, request=request)
    return httpx.Response(status_code, json=body, request=request)


def _registration(discord_id: str, **fields) -> MembershipApplication:
    """Create a registration.

    Returns:
        The registration.

    """
    return MembershipApplication.objects.create(discord_id=discord_id, discord_username=f"u{discord_id}", **fields)


@pytest.fixture
def registrations(db):
    """One verified, one with only a typed zwid, one that never touched Zwift.

    Returns:
        ``(verified, typed, untouched)``.

    """
    return (
        _registration("1", zwift_id="4242", zwift_verified=True),
        _registration("2", zwift_id="5151"),
        _registration("3"),
    )


# --- the helper ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_only_registrations_the_service_holds_links_for_are_disconnected(registrations, service):
    """A bulk delete of never-connected registrations must not become one call each."""
    verified, _typed, _untouched = registrations
    service["linked"] = {str(verified.pk)}

    counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert service["disconnects"] == [str(verified.pk)]
    assert service["list_calls"] == 1
    assert counts == {"attempted": 1, "removed": 1, "failed": 0}


@pytest.mark.django_db
def test_a_link_the_local_flags_do_not_show_is_still_dropped(registrations, service):
    """Consent can finish without the page being reloaded to record it on the row."""
    untouched = registrations[2]
    service["linked"] = {str(untouched.pk)}

    counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert service["disconnects"] == [str(untouched.pk)]
    assert counts["removed"] == 1


@pytest.mark.django_db
def test_links_belonging_to_other_ids_are_left_alone(registrations, service):
    service["linked"] = {"17", "an-unrelated-registration"}

    counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert service["disconnects"] == []
    assert counts == {"attempted": 0, "removed": 0, "failed": 0}


@pytest.mark.django_db
def test_nothing_to_delete_asks_nothing(service):
    counts = release_application_zwift_links(MembershipApplication.objects.none())

    assert counts == {"attempted": 0, "removed": 0, "failed": 0}
    assert service["list_calls"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param({"side_effect": httpx.ConnectError("refused")}, id="unreachable"),
        pytest.param({"return_value": _response("POST", "/api/zwift/oauth/disconnect", 500)}, id="server-error"),
        pytest.param({"return_value": _response("POST", "/api/zwift/oauth/disconnect", 200, ["?"])}, id="odd-body"),
    ],
)
def test_a_disconnect_the_service_did_not_confirm_is_a_failure(registrations, configured, failure):
    verified = registrations[0]
    listing = _response("GET", "/api/zwift/oauth/connections", 200, [{"user_id": str(verified.pk), "zwid": "4242"}])

    with (
        patch("apps.zwift.client.httpx.get", return_value=listing),
        patch("apps.zwift.client.httpx.post", **failure),
    ):
        counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert counts == {"attempted": 1, "removed": 0, "failed": 1}


@pytest.mark.django_db
def test_when_the_service_cannot_be_asked_every_possible_link_is_a_failure(registrations, configured):
    """The verified row and the typed-zwid row may hold links; nothing confirms they do not."""
    verified, typed, _untouched = registrations
    post = MagicMock()

    with (
        patch("apps.zwift.client.httpx.get", side_effect=httpx.ConnectError("refused")),
        patch("apps.zwift.client.httpx.post", post),
        patch("apps.team.services.logfire") as fake_logfire,
    ):
        counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert counts == {"attempted": 0, "removed": 0, "failed": 2}
    post.assert_not_called()  # no timeout per row while the service is down
    # The ids are the only way to find those links in the service afterwards.
    logged = fake_logfire.error.call_args.kwargs["application_ids"]
    assert logged == sorted([str(verified.pk), str(typed.pk)])


@pytest.mark.django_db
def test_an_unconfigured_service_confirms_nothing_either(registrations, monkeypatch):
    monkeypatch.setattr(config, "zwift_api_base_url", None)
    monkeypatch.setattr(config, "zwift_app_api_key", None)

    counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert counts == {"attempted": 0, "removed": 0, "failed": 2}


@pytest.mark.django_db
def test_an_unexpected_exception_is_counted_not_raised(registrations, service, monkeypatch):
    verified = registrations[0]
    service["linked"] = {str(verified.pk)}

    def boom(uid):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("apps.zwift.client.disconnect_link", boom)

    counts = release_application_zwift_links(MembershipApplication.objects.all())

    assert counts == {"attempted": 1, "removed": 0, "failed": 1}


# --- the delete paths ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_single_delete_view_drops_the_link(client, membership_admin, registrations, service):
    verified = registrations[0]
    service["linked"] = {str(verified.pk)}
    client.force_login(membership_admin)

    client.post(reverse("team:application_delete", args=[verified.pk]))

    assert service["disconnects"] == [str(verified.pk)]
    assert not MembershipApplication.objects.filter(pk=verified.pk).exists()


@pytest.mark.django_db
def test_the_single_delete_view_skips_a_registration_with_no_link(client, membership_admin, registrations, service):
    untouched = registrations[2]
    client.force_login(membership_admin)

    client.post(reverse("team:application_delete", args=[untouched.pk]))

    assert service["disconnects"] == []
    assert not MembershipApplication.objects.filter(pk=untouched.pk).exists()


@pytest.mark.django_db
def test_the_bulk_delete_view_drops_the_links(client, membership_admin, registrations, service):
    service["linked"] = {str(registrations[0].pk), str(registrations[1].pk)}
    client.force_login(membership_admin)

    client.post(reverse("team:application_bulk_delete"), {"selected": [str(r.pk) for r in registrations]})

    assert len(service["disconnects"]) == 2
    assert service["list_calls"] == 1
    assert not MembershipApplication.objects.exists()


@pytest.mark.django_db
def test_the_delete_logs_carry_no_names(client, membership_admin, registrations, service):
    verified = registrations[0]
    verified.first_name = "Nova"
    verified.save(update_fields=["first_name"])
    service["linked"] = {str(verified.pk)}
    client.force_login(membership_admin)

    with patch("apps.team.views.logfire") as fake_logfire:
        client.post(reverse("team:application_delete", args=[verified.pk]))

    kwargs = fake_logfire.info.call_args.kwargs
    assert kwargs["application_id"] == str(verified.pk)
    assert kwargs["zwift_link_removed"] is True
    assert "Nova" not in str(fake_logfire.mock_calls)
    assert membership_admin.username not in str(fake_logfire.mock_calls)


@pytest.mark.django_db
def test_the_admin_delete_drops_the_link(superuser, registrations, service):
    verified = registrations[0]
    pk = verified.pk  # delete() clears it on the instance
    service["linked"] = {str(pk)}
    request = RequestFactory().post("/admin/")
    request.user = superuser

    site._registry[MembershipApplication].delete_model(request, verified)

    assert service["disconnects"] == [str(pk)]
    assert not MembershipApplication.objects.filter(pk=pk).exists()


@pytest.mark.django_db
def test_the_admin_bulk_delete_drops_the_links(superuser, registrations, service):
    service["linked"] = {str(registrations[0].pk), str(registrations[1].pk)}
    request = RequestFactory().post("/admin/")
    request.user = superuser

    site._registry[MembershipApplication].delete_queryset(request, MembershipApplication.objects.all())

    assert len(service["disconnects"]) == 2
    assert not MembershipApplication.objects.exists()


@pytest.fixture
def unreachable(configured):
    """Make the real client's calls fail as if the service were down.

    Yields:
        Nothing; the patches are active while the test runs.

    """
    with (
        patch("apps.zwift.client.httpx.get", side_effect=httpx.ConnectError("down")),
        patch("apps.zwift.client.httpx.post", side_effect=httpx.ConnectError("down")),
    ):
        yield


@pytest.mark.django_db
def test_the_delete_view_warns_when_a_link_may_survive(client, membership_admin, registrations, unreachable):
    verified = registrations[0]
    client.force_login(membership_admin)

    response = client.post(reverse("team:application_delete", args=[verified.pk]), follow=True)

    shown = [str(m) for m in response.context["messages"]]
    assert ZWIFT_LINK_NOT_RELEASED_MESSAGE in shown
    assert not MembershipApplication.objects.filter(pk=verified.pk).exists()


@pytest.mark.django_db
def test_the_bulk_delete_view_warns_when_a_link_may_survive(client, membership_admin, registrations, unreachable):
    client.force_login(membership_admin)

    response = client.post(
        reverse("team:application_bulk_delete"), {"selected": [str(r.pk) for r in registrations]}, follow=True
    )

    assert ZWIFT_LINK_NOT_RELEASED_MESSAGE in [str(m) for m in response.context["messages"]]


@pytest.mark.django_db
def test_the_delete_view_says_nothing_extra_when_the_links_went(client, membership_admin, registrations, service):
    verified = registrations[0]
    service["linked"] = {str(verified.pk)}
    client.force_login(membership_admin)

    response = client.post(reverse("team:application_delete", args=[verified.pk]), follow=True)

    assert ZWIFT_LINK_NOT_RELEASED_MESSAGE not in [str(m) for m in response.context["messages"]]


def _admin_request(user):
    """Build an admin POST request that can carry messages.

    Returns:
        The request.

    """
    request = RequestFactory().post("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["delete_model", "delete_queryset"])
def test_the_admin_warns_when_a_link_may_survive(superuser, registrations, unreachable, path):
    request = _admin_request(superuser)
    model_admin = site._registry[MembershipApplication]

    if path == "delete_model":
        model_admin.delete_model(request, registrations[0])
    else:
        model_admin.delete_queryset(request, MembershipApplication.objects.all())

    assert ZWIFT_LINK_NOT_RELEASED_MESSAGE in [str(m) for m in request._messages]


@pytest.mark.django_db
def test_account_deletion_drops_the_registration_link_too(user_model, service):
    """The account's own link and the registration's are separate, and both have to go."""
    member = user_model.objects.create_user(username="leaving", discord_id="1")
    registration = _registration("1", zwift_id="4242", zwift_verified=True)
    member_pk = member.pk
    service["linked"] = {str(member_pk), str(registration.pk)}

    audit = delete_user_account(member)

    assert sorted(service["disconnects"]) == sorted([str(member_pk), str(registration.pk)])
    assert audit["zauth_disconnected"] is True
    assert audit["application_zwift_links_removed"] == 1
    assert audit["complete"] is True
    assert not MembershipApplication.objects.filter(discord_id="1").exists()


@pytest.mark.django_db
def test_an_outage_leaves_the_account_deletion_incomplete(user_model, configured):
    """The real client swallows the HTTP error; the erasure must still say it did not finish."""
    member = user_model.objects.create_user(username="leaving", discord_id="1")
    _registration("1", zwift_verified=True)

    with (
        patch("apps.zwift.client.httpx.get", side_effect=httpx.ConnectError("down")),
        patch("apps.zwift.client.httpx.post", side_effect=httpx.ConnectError("down")),
    ):
        audit = delete_user_account(member)

    assert audit["complete"] is False
    assert audit["application_zwift_links_failed"] == 1
    assert any("membership registration" in reason for reason in audit["incomplete_reasons"])
    assert any("upstream Zwift link" in reason for reason in audit["incomplete_reasons"])
    assert not MembershipApplication.objects.filter(discord_id="1").exists()  # erased regardless


@pytest.mark.django_db
def test_a_refused_registration_disconnect_leaves_the_account_deletion_incomplete(user_model, configured):
    member = user_model.objects.create_user(username="leaving", discord_id="1")
    registration = _registration("1", zwift_verified=True)
    listing = _response("GET", "/api/zwift/oauth/connections", 200, [{"user_id": str(registration.pk)}])

    def post(url, json, **kwargs):
        if json["user_id"] == str(registration.pk):
            return _response("POST", "/api/zwift/oauth/disconnect", 503)
        return _response("POST", "/api/zwift/oauth/disconnect", 200, {"disconnected": False})

    with (
        patch("apps.zwift.client.httpx.get", return_value=listing),
        patch("apps.zwift.client.httpx.post", side_effect=post),
    ):
        audit = delete_user_account(member)

    assert audit["zauth_disconnected"] is False  # the account itself simply had no link
    assert audit["application_zwift_links_failed"] == 1
    assert audit["complete"] is False
    assert audit["incomplete_reasons"] == ["the Zwift link made from the membership registration could not be dropped"]
