"""Re-reading a rider's Zwift weight and height when they matter.

Two triggers, one task: a rider submitting a weight or height verification, and a reviewer
pressing "Refresh height and weight" on the record. Both enqueue ``refresh_zwift_profile``,
which asks zauth's ``POST /api/zwift/users/{user_id}/profile/refresh`` to read Zwift now, then
pulls the rider's ``RiderProfile`` so the cached card catches up too.

Enqueues are asserted against the real database task backend rather than a mock, so these
tests also prove the arguments survive being stored as JSON. HTTP is patched at the client.
"""

from unittest.mock import patch

import httpx
import pytest
from django.apps import apps as django_apps
from django.urls import reverse

from apps.rider_data import tasks
from apps.team.forms import RaceReadyRecordForm
from apps.team.models import RaceReadyRecord
from apps.zwift import client

_REFRESHED = {"zwid": "4242", "profile_weight_in_grams": 71500, "height_in_millimeters": 1780, "refreshed": True}


def _enqueued(task_name: str) -> list[dict]:
    """List the stored calls of one task, oldest first.

    Args:
        task_name: The task function's name.

    Returns:
        Each enqueued call's ``{"args": [...], "kwargs": {...}}``.

    """
    results = django_apps.get_model("django_tasks_database", "DBTaskResult")
    rows = results.objects.filter(task_path__endswith=f".{task_name}").order_by("enqueued_at")
    return [row.args_kwargs for row in rows]


def _response(status: int, document: object = None) -> httpx.Response:
    """Build a service response that knows its request.

    Args:
        status: The HTTP status.
        document: The JSON body, or None for an empty one.

    Returns:
        The response.

    """
    request = httpx.Request("POST", "http://zauth.test/api/zwift/users/7/profile/refresh")
    if document is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, json=document, request=request)


@pytest.fixture
def configured():
    """Point the app-scoped client at a fake service."""
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client.config, "zwift_api_base_url", "http://zauth.test"),
        patch.object(client.config, "zwift_app_api_key", "app-key"),
    ):
        yield


@pytest.fixture
def reviewer(user_model):
    """Build a reviewer who can open verification records.

    Returns:
        The reviewer.

    """
    return user_model.objects.create_user(
        username="reviewer",
        email="reviewer@example.test",
        gender="male",
        permission_overrides={"team_member": True, "approve_verification": True},
    )


@pytest.fixture
def rider(user_model):
    """Build a Zwift-verified rider.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider",
        email="rider@example.test",
        gender="male",
        zwid=4242,
        zwid_verified=True,
        permission_overrides={"team_member": True},
    )


@pytest.fixture
def connected(monkeypatch):
    """Make the review page's Zwift Auth panel see a connected rider."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr(
        "apps.zwift.client.get_connection_status",
        lambda uid: {"connected": True, "zwid": "4242", "connected_at": "2026-05-01T09:30:00Z"},
    )
    monkeypatch.setattr(
        "apps.zwift.client.get_racing_profile",
        lambda uid: {"profile_weight_in_grams": 71500, "fetched_at": "2026-09-16T08:00:00Z", "data": {}},
    )
    monkeypatch.setattr("apps.zwift.client.get_profile_stats", lambda uid: None)


# --- the client ------------------------------------------------------------------------


def test_the_refresh_posts_to_the_users_refresh_endpoint_with_the_app_key(configured):
    """It is app-scoped: our user id in the path, our per-app key in the header."""
    with patch("httpx.post", return_value=_response(200, _REFRESHED)) as post:
        assert client.refresh_racing_profile("7") == _REFRESHED

    assert post.call_args.args[0] == "http://zauth.test/api/zwift/users/7/profile/refresh"
    assert post.call_args.kwargs["headers"] == {"X-API-Key": "app-key"}


def test_an_unconfigured_client_makes_no_call():
    """Local dev has no service; a submission must not fail for want of one."""
    with patch.object(client, "is_configured", return_value=False), patch("httpx.post") as post:
        assert client.refresh_racing_profile("7") is None
    post.assert_not_called()


@pytest.mark.parametrize("status", [404, 429, 502])
def test_a_refused_refresh_reports_none(configured, status):
    """Not connected, rate limited, upstream down: none of them is a profile."""
    with patch("httpx.post", return_value=_response(status, {"detail": "no"})):
        assert client.refresh_racing_profile("7") is None


def test_an_unreachable_service_reports_none(configured):
    """It runs in a worker, where an exception would only fail the task with nothing to show for it."""
    with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
        assert client.refresh_racing_profile("7") is None


def test_a_body_that_is_not_json_reports_none(configured):
    """A proxy error page answering 200 must not raise out of the client."""
    request = httpx.Request("POST", "http://zauth.test/x")
    with patch("httpx.post", return_value=httpx.Response(200, text="<html>", request=request)):
        assert client.refresh_racing_profile("7") is None


# --- the task --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_refreshed_profile_is_pulled_into_the_cache():
    """The service has stored the new values by the time it answers, so the pull can go at once."""
    with patch.object(client, "refresh_racing_profile", return_value=_REFRESHED) as refresh:
        outcome = tasks.refresh_zwift_profile.call(7)

    refresh.assert_called_once_with("7")
    assert outcome == {"reached": True, "refreshed": True, "zwid": 4242}
    assert _enqueued("pull_rider_profile") == [{"args": [4242], "kwargs": {}}]


@pytest.mark.django_db
def test_a_throttled_refresh_still_pulls():
    """Read under a minute ago upstream, but our cached copy can be older than that."""
    with patch.object(client, "refresh_racing_profile", return_value={**_REFRESHED, "refreshed": False}):
        outcome = tasks.refresh_zwift_profile.call(7)

    assert outcome["refreshed"] is False
    assert _enqueued("pull_rider_profile") == [{"args": [4242], "kwargs": {}}]


@pytest.mark.django_db
def test_a_failed_refresh_pulls_nothing():
    """The service kept its stored profile, so there is nothing new to bring over."""
    with patch.object(client, "refresh_racing_profile", return_value=None):
        outcome = tasks.refresh_zwift_profile.call(7)

    assert outcome == {"reached": False, "refreshed": False, "zwid": None}
    assert _enqueued("pull_rider_profile") == []


@pytest.mark.django_db
@pytest.mark.parametrize("zwid", [None, "", "abc", "٣", 0])
def test_a_profile_without_a_usable_zwid_pulls_nothing(zwid):
    """The cache is keyed by zwid; without one there is no row to update."""
    with patch.object(client, "refresh_racing_profile", return_value={**_REFRESHED, "zwid": zwid}):
        outcome = tasks.refresh_zwift_profile.call(7)

    assert outcome["reached"] is True
    assert outcome["zwid"] is None
    assert _enqueued("pull_rider_profile") == []


@pytest.mark.django_db
def test_a_numeric_zwid_is_accepted_too():
    """The schema says string; an integer is the same rider and should not be dropped."""
    with patch.object(client, "refresh_racing_profile", return_value={**_REFRESHED, "zwid": 4242}):
        assert tasks.refresh_zwift_profile.call(7)["zwid"] == 4242


# --- submitting a verification ---------------------------------------------------------


def _submit(client_, verify_type: str):
    """Post a verification record the way the rider's form does.

    Args:
        client_: A logged-in test client.
        verify_type: The record type.

    Returns:
        The response.

    """
    # A link where the verification takes one, else a photo given by URL -- Weight Light is a
    # photo or Other, never a link. Not Other: that carries no link at all, only a note.
    accepted = RaceReadyRecordForm.MEDIA_TYPES_BY_VERIFY_TYPE[verify_type]
    data = {
        "verify_type": verify_type,
        "media_type": "link" if "link" in accepted else "photo",
        "url": "https://example.test/evidence",
        "record_date": "2026-09-01",
    }
    if verify_type.startswith("weight"):
        data["weight"] = "71.5"
    elif verify_type == "height":
        data["height"] = "178"
    return client_.post(reverse("accounts:submit_race_ready"), data)


@pytest.mark.django_db
@pytest.mark.parametrize("verify_type", ["weight_light", "height"])
def test_submitting_a_weight_or_height_asks_zwift_for_the_current_values(client, rider, verify_type):
    """The reviewer should compare the claim with Zwift as it is at submission."""
    client.force_login(rider)

    _submit(client, verify_type)

    assert RaceReadyRecord.objects.filter(user=rider, verify_type=verify_type).exists()
    assert _enqueued("refresh_zwift_profile") == [{"args": [], "kwargs": {"user_id": rider.id}}]


@pytest.mark.django_db
def test_submitting_power_does_not(client, rider):
    """Zwift's profile carries no power claim to compare against."""
    client.force_login(rider)

    _submit(client, "power")

    assert RaceReadyRecord.objects.filter(user=rider, verify_type="power").exists()
    assert _enqueued("refresh_zwift_profile") == []


@pytest.mark.django_db
def test_an_invalid_submission_does_not(client, rider):
    """Nothing was submitted, so there is nothing to compare."""
    client.force_login(rider)

    client.post(reverse("accounts:submit_race_ready"), {"verify_type": "height", "media_type": "link"})

    assert not RaceReadyRecord.objects.filter(user=rider).exists()
    assert _enqueued("refresh_zwift_profile") == []


# --- the review page button ------------------------------------------------------------


def _refresh(client_, record):
    """Press the review page's refresh button.

    Args:
        client_: A logged-in test client.
        record: The record being reviewed.

    Returns:
        The response.

    """
    return client_.post(
        reverse("team:verification_record_detail", args=[record.pk]), {"action": "refresh_zwift_profile"}
    )


@pytest.mark.django_db
@pytest.mark.parametrize(("verify_type", "value"), [("weight_full", {"weight": 71.5}), ("height", {"height": 178})])
def test_the_button_refreshes_the_rider_and_says_to_wait(
    client, reviewer, rider, verification_factory, verify_type, value
):
    """The reviewer is told it takes a minute, and lands back on the same record."""
    record = verification_factory(rider, verify_type, status=RaceReadyRecord.Status.PENDING, **value)
    client.force_login(reviewer)

    response = _refresh(client, record)

    assert response.status_code == 302
    assert response.url == reverse("team:verification_record_detail", args=[record.pk])
    assert _enqueued("refresh_zwift_profile") == [{"args": [], "kwargs": {"user_id": rider.id}}]
    shown = " ".join(str(m) for m in client.get(response.url).context["messages"])
    assert "can take a minute" in shown


@pytest.mark.django_db
def test_the_button_does_not_change_the_record(client, reviewer, rider, verification_factory):
    """It must not fall through to the review actions below it."""
    record = verification_factory(rider, "weight_full", status=RaceReadyRecord.Status.PENDING, weight=71.5)
    client.force_login(reviewer)

    _refresh(client, record)

    record.refresh_from_db()
    assert record.status == RaceReadyRecord.Status.PENDING
    assert record.reviewed_by is None


@pytest.mark.django_db
def test_a_reviewed_record_can_be_refreshed_too(client, reviewer, rider, verification_factory):
    """Checking a past decision against Zwift is as legitimate as checking a new one."""
    record = verification_factory(rider, "height", status=RaceReadyRecord.Status.VERIFIED, height=178)
    client.force_login(reviewer)

    _refresh(client, record)

    assert _enqueued("refresh_zwift_profile") == [{"args": [], "kwargs": {"user_id": rider.id}}]


@pytest.mark.django_db
def test_a_power_record_refreshes_nothing(client, reviewer, rider, verification_factory):
    """No button is drawn for power, so this is a stale or hand-made POST."""
    record = verification_factory(rider, "power", status=RaceReadyRecord.Status.PENDING, ftp=300)
    client.force_login(reviewer)

    response = _refresh(client, record)

    assert response.url == reverse("team:verification_record_detail", args=[record.pk])
    assert _enqueued("refresh_zwift_profile") == []


@pytest.mark.django_db
def test_someone_who_cannot_review_cannot_refresh(client, team_member, rider, verification_factory):
    """The page's permission check stays ahead of the new action."""
    record = verification_factory(rider, "height", status=RaceReadyRecord.Status.PENDING, height=178)
    client.force_login(team_member)

    _refresh(client, record)

    assert _enqueued("refresh_zwift_profile") == []


@pytest.mark.django_db
def test_the_same_gender_rule_still_applies(client, reviewer, user_model, verification_factory):
    """A record the reviewer may not open is not one they may act on."""
    other = user_model.objects.create_user(username="other", email="other@example.test", gender="female")
    record = verification_factory(other, "height", status=RaceReadyRecord.Status.PENDING, height=165)
    RaceReadyRecord.objects.filter(pk=record.pk).update(same_gender=True)
    client.force_login(reviewer)

    _refresh(client, record)

    assert _enqueued("refresh_zwift_profile") == []


@pytest.mark.django_db
@pytest.mark.parametrize("verify_type", ["weight_full", "weight_light", "height"])
def test_the_button_is_shown_for_a_connected_riders_measurement(
    client, reviewer, rider, verification_factory, connected, verify_type
):
    """Weight and height are the two values Zwift's profile holds."""
    record = verification_factory(rider, verify_type, status=RaceReadyRecord.Status.PENDING, weight=71.5, height=178)
    client.force_login(reviewer)

    page = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "Refresh height and weight" in page
    assert 'value="refresh_zwift_profile"' in page


@pytest.mark.django_db
def test_the_button_is_not_shown_for_power(client, reviewer, rider, verification_factory, connected):
    """There is nothing in Zwift's profile to compare a power claim with."""
    record = verification_factory(rider, "power", status=RaceReadyRecord.Status.PENDING, ftp=300)
    client.force_login(reviewer)

    page = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "Refresh height and weight" not in page


@pytest.mark.django_db
def test_the_button_is_not_shown_for_a_rider_who_is_not_connected(
    client, reviewer, rider, verification_factory, monkeypatch
):
    """Without a connection the service has no profile to refresh."""
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", lambda uid: {"connected": False})
    record = verification_factory(rider, "height", status=RaceReadyRecord.Status.PENDING, height=178)
    client.force_login(reviewer)

    page = client.get(reverse("team:verification_record_detail", args=[record.pk])).content.decode()

    assert "Not connected" in page
    assert "Refresh height and weight" not in page
