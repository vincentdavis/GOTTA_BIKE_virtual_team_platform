"""The on-demand "Update" path: trigger zauth, then come back for the result.

The shape worth pinning is the split. ``client.request_refresh`` only *asks* the service to
re-read ZwiftPower and zwiftracing; the service answers immediately and does the fetching on
its own worker, so the fresh document is not in that response and has to be pulled separately.
Collapsing the two -- treating a 200 as "updated" -- would show a rider stale numbers under a
success message, which is worse than showing them stale numbers.

The retry is watching one specific stamp, and which one is not arbitrary: see
``test_the_pull_watches_the_one_stamp_that_actually_moves``.
"""

from unittest.mock import patch

import httpx
import pytest
from django.utils import timezone

from apps.rider_data import client, tasks
from apps.rider_data.models import RiderProfile

# Bound here on purpose. The retry re-enqueues through the module global, so the tests below
# patch `tasks.pull_rider_profile` -- which would also swallow the call under test if they
# reached the task through the same name.
from apps.rider_data.tasks import pull_rider_profile


def _row(zwid=4242, zr_stamp="2026-09-01T10:00:00Z"):
    """Store a cached profile carrying a zwiftracing fetch stamp.

    Args:
        zwid: The rider's Zwift id.
        zr_stamp: The stamp to record, or "" for a source never fetched.

    Returns:
        The stored row.

    """
    now = timezone.now()
    return RiderProfile.objects.create(
        zwid=zwid,
        payload={"zwid": zwid},
        sources={"zwiftracing": {"present": True, "fetched_at": zr_stamp}} if zr_stamp else {},
        fetched_at=now,
        last_requested_at=now,
    )


def _response(document, url="http://zauth.test/api/riders/4242/refresh"):
    """Build an httpx response that can answer raise_for_status.

    Args:
        document: The JSON body.
        url: The URL the response is pretending to answer.

    Returns:
        The response.

    """
    return httpx.Response(200, json=document, request=httpx.Request("POST", url))


def _answer(zwiftpower="queued", zwiftracing="queued"):
    """Build a service refresh response.

    Args:
        zwiftpower: Status for the ZwiftPower source.
        zwiftracing: Status for the zwiftracing source.

    Returns:
        The response document.

    """
    return {
        "zwid": 4242,
        "zwiftpower": {"status": zwiftpower, "last_updated": None},
        "zwiftracing": {"status": zwiftracing, "last_updated": None},
    }


# --- the trigger call ----------------------------------------------------------------


def test_the_trigger_posts_to_the_rider_refresh_endpoint():
    """The zwid goes in the path and the sources filter in the query, as the service declares."""
    response = _response(_answer())
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client.config, "zwift_api_base_url", "http://zauth.test"),
        patch("httpx.post", return_value=response) as post,
    ):
        assert client.request_refresh(4242, sources="zwiftracing") == _answer()

    assert post.call_args.args[0] == "http://zauth.test/api/riders/4242/refresh"
    assert post.call_args.kwargs["params"] == {"sources": "zwiftracing"}


def test_no_sources_filter_is_sent_when_none_is_asked_for():
    """The service defaults to both; sending sources=None would be a filter matching nothing."""
    with (
        patch.object(client, "is_configured", return_value=True),
        patch("httpx.post", return_value=_response(_answer())) as post,
    ):
        client.request_refresh(4242)

    assert post.call_args.kwargs["params"] is None


def test_an_unconfigured_client_makes_no_call():
    """Local dev has no service key; the button must degrade rather than raise."""
    with patch.object(client, "is_configured", return_value=False), patch("httpx.post") as post:
        assert client.request_refresh(4242) is None
    post.assert_not_called()


def test_a_failed_trigger_reports_none_rather_than_raising():
    """A view is on the other end of this; an unreachable service is a message, not a 500."""
    with (
        patch.object(client, "is_configured", return_value=True),
        patch("httpx.post", side_effect=httpx.ConnectError("boom")),
    ):
        assert client.request_refresh(4242) is None


# --- trigger + schedule together -----------------------------------------------------


@pytest.mark.django_db
def test_a_queued_source_schedules_a_delayed_pull_that_waits_for_it():
    """Pulling straight away would fetch the same document the service has not replaced yet."""
    _row(zr_stamp="2026-09-01T10:00:00Z")
    with (
        patch.object(client, "request_refresh", return_value=_answer()),
        patch.object(tasks, "pull_rider_profile") as pull,
    ):
        outcome = tasks.request_profile_refresh(4242)

    assert outcome["reached"] is True
    assert sorted(outcome["queued"]) == ["zwiftpower", "zwiftracing"]

    run_after = pull.using.call_args.kwargs["run_after"]
    assert (run_after - timezone.now()).total_seconds() > tasks.PULL_DELAY_SECONDS - 5
    pull.using.return_value.enqueue.assert_called_once_with(
        4242,
        await_zwiftracing=True,
        zr_baseline="2026-09-01T10:00:00Z",
    )


@pytest.mark.django_db
def test_a_fully_throttled_trigger_still_pulls_immediately():
    """Skipped upstream means "no new fetch", not "nothing to collect" -- our copy can be older."""
    _row()
    with (
        patch.object(client, "request_refresh", return_value=_answer("skipped", "skipped")),
        patch.object(tasks, "pull_rider_profile") as pull,
    ):
        outcome = tasks.request_profile_refresh(4242)

    assert outcome["queued"] == []
    assert (pull.using.call_args.kwargs["run_after"] - timezone.now()).total_seconds() < 5
    assert pull.using.return_value.enqueue.call_args.kwargs["await_zwiftracing"] is False


@pytest.mark.django_db
def test_an_unreachable_service_is_reported_but_the_pull_is_still_scheduled():
    """The rider pressed a button; collecting whatever the service already holds is still worth it."""
    with (
        patch.object(client, "request_refresh", return_value=None),
        patch.object(tasks, "pull_rider_profile") as pull,
    ):
        outcome = tasks.request_profile_refresh(4242)

    assert outcome["reached"] is False
    assert outcome["statuses"] == {"zwiftpower": "unknown", "zwiftracing": "unknown"}
    pull.using.return_value.enqueue.assert_called_once()


# --- the pull, and what it waits on --------------------------------------------------


@pytest.mark.django_db
def test_the_pull_watches_the_one_stamp_that_actually_moves():
    """Only the zwiftracing stamp actually advances, so it is the only usable signal.

    The zwiftpower half of a refresh runs the rider's race *history* fetch, which writes result
    rows. ``sources.zwiftpower.fetched_at`` comes from the rider row, which that fetch does not
    touch -- so watching it would mean waiting for something that never happens.
    """
    _row(zr_stamp="2026-09-01T10:00:00Z")
    fresh = {
        "zwid": 4242,
        "sources": {"zwiftracing": {"present": True, "fetched_at": "2026-09-02T09:00:00Z"}},
    }
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "fetch_profiles", return_value=[fresh]),
        patch.object(tasks, "pull_rider_profile") as retry,
    ):
        result = pull_rider_profile.func(4242, await_zwiftracing=True, zr_baseline="2026-09-01T10:00:00Z")

    assert result["landed"] is True
    retry.using.assert_not_called()


@pytest.mark.django_db
def test_the_pull_comes_back_while_the_stamp_has_not_moved():
    """The service's worker sets the pace; a single fixed wait would miss a slow fetch entirely."""
    _row(zr_stamp="2026-09-01T10:00:00Z")
    same = {"zwid": 4242, "sources": {"zwiftracing": {"present": True, "fetched_at": "2026-09-01T10:00:00Z"}}}
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "fetch_profiles", return_value=[same]),
        patch.object(tasks, "pull_rider_profile") as retry,
    ):
        result = pull_rider_profile.func(4242, await_zwiftracing=True, zr_baseline="2026-09-01T10:00:00Z")

    assert result["landed"] is False
    retry.using.return_value.enqueue.assert_called_once_with(
        4242,
        await_zwiftracing=True,
        zr_baseline="2026-09-01T10:00:00Z",
        attempt=2,
    )


@pytest.mark.django_db
def test_the_pull_gives_up_after_the_last_attempt():
    """Unbounded retries would have a rider's button quietly queueing work for the rest of the day."""
    _row(zr_stamp="2026-09-01T10:00:00Z")
    same = {"zwid": 4242, "sources": {"zwiftracing": {"present": True, "fetched_at": "2026-09-01T10:00:00Z"}}}
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "fetch_profiles", return_value=[same]),
        patch.object(tasks, "pull_rider_profile") as retry,
    ):
        pull_rider_profile.func(
            4242,
            await_zwiftracing=True,
            zr_baseline="2026-09-01T10:00:00Z",
            attempt=tasks.MAX_PULL_ATTEMPTS,
        )

    retry.using.assert_not_called()


@pytest.mark.django_db
def test_a_pull_with_nothing_in_flight_runs_once():
    """Nothing was queued upstream, so a second look would return the same document."""
    _row()
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "fetch_profiles", return_value=[]),
        patch.object(tasks, "pull_rider_profile") as retry,
    ):
        result = pull_rider_profile.func(4242, await_zwiftracing=False)

    assert result["landed"] is True
    retry.using.assert_not_called()


@pytest.mark.django_db
def test_the_pull_stores_what_it_fetched():
    """The whole point of the round trip: the row a teammate then sees is the refreshed one."""
    doc = {
        "zwid": 4242,
        "identity": {"name": "Ada Racer"},
        "ratings": {"velo": 1610.0},
        "sources": {"zwiftracing": {"present": True, "fetched_at": "2026-09-02T09:00:00Z"}},
    }
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "fetch_profiles", return_value=[doc]),
    ):
        pull_rider_profile.func(4242)

    row = RiderProfile.objects.get(zwid=4242)
    assert row.name == "Ada Racer"
    assert row.velo == pytest.approx(1610.0)
