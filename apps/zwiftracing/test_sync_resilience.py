"""`sync_zr_riders` has to survive a slow or flaky Zwift Racing API.

The task paginates by re-enqueueing itself, so an unhandled network error doesn't just
lose one page -- it ends the chain, leaving the roster half-synced with nothing to
resume it. These tests pin the retry that keeps the chain alive, and the bound that
stops it retrying into a sustained outage.
"""

from unittest.mock import patch

import httpx
import pytest

from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.tasks import _MAX_FETCH_ATTEMPTS, sync_zr_riders

# The task re-enqueues itself by module-global name, and a django-tasks Task can't be
# patched attribute-wise -- so hold the plain function and swap the global underneath it.
_run = sync_zr_riders.func


@pytest.mark.django_db
def test_a_read_timeout_is_retried_not_fatal() -> None:
    """The exact failure from production: httpx.ReadTimeout out of get_club."""
    with (
        patch("apps.zwiftracing.tasks.get_club", side_effect=httpx.ReadTimeout("timed out")),
        patch("apps.zwiftracing.tasks.sync_zr_riders") as task,
    ):
        result = _run(from_id=4598636)

    assert result["status"] == "retrying"
    assert result["from_id"] == 4598636
    task.using.return_value.enqueue.assert_called_once_with(4598636, 1)


@pytest.mark.django_db
def test_the_retry_resumes_the_same_page() -> None:
    """Resuming anywhere else would skip riders outright."""
    with (
        patch("apps.zwiftracing.tasks.get_club", side_effect=httpx.ConnectError("refused")),
        patch("apps.zwiftracing.tasks.sync_zr_riders") as task,
    ):
        _run(from_id=999, attempt=1)

    task.using.return_value.enqueue.assert_called_once_with(999, 2)


@pytest.mark.django_db
def test_it_gives_up_after_the_bound() -> None:
    """A sustained outage must not requeue forever."""
    with (
        patch("apps.zwiftracing.tasks.get_club", side_effect=httpx.ReadTimeout("timed out")),
        patch("apps.zwiftracing.tasks.sync_zr_riders") as task,
    ):
        result = _run(from_id=1, attempt=_MAX_FETCH_ATTEMPTS - 1)

    assert result["status"] == "failed"
    task.using.return_value.enqueue.assert_not_called()


@pytest.mark.django_db
def test_a_successful_page_still_syncs() -> None:
    """The retry wrapper must not disturb the normal path."""
    payload = {"riders": [{"riderId": 12345, "name": "Alice Rider"}]}
    with patch("apps.zwiftracing.tasks.get_club", return_value=(200, payload)):
        result = _run()

    assert result["status"] == "complete"
    assert ZRRider.objects.filter(zwid=12345).exists()


@pytest.mark.django_db
def test_rate_limiting_is_untouched() -> None:
    """429 has its own retry-after path; the network retry must not shadow it."""
    with (
        patch("apps.zwiftracing.tasks.get_club", return_value=(429, {"retryAfter": 42})),
        patch("apps.zwiftracing.tasks.sync_zr_riders") as task,
    ):
        result = _run(from_id=7)

    assert result["status"] == "rate_limited"
    assert result["retry_after"] == 42
    task.using.return_value.enqueue.assert_called_once_with(7)


def test_the_client_sets_an_explicit_timeout() -> None:
    """The 5s httpx default is what the club endpoint routinely exceeds."""
    from apps.zwiftracing import zr_client

    assert zr_client._TIMEOUT.read >= 30
    assert zr_client._TIMEOUT.connect <= 15


@pytest.mark.django_db
def test_warm_club_survives_the_same_failure() -> None:
    """The ladder planner's club warmer paginates the same way, so it broke the same way."""
    from apps.ladder_planner.tasks import warm_club

    run = warm_club.func
    with (
        patch("apps.ladder_planner.tasks.get_club", side_effect=httpx.ReadTimeout("timed out")),
        patch("apps.ladder_planner.tasks.warm_club") as task,
    ):
        result = run(club_id=20650, from_id=555, _accumulated=1200)

    assert result["status"] == "retrying"
    # _accumulated has to survive, or the retry restates the running total.
    task.using.return_value.enqueue.assert_called_once_with(20650, 555, 1200, 1)


@pytest.mark.django_db
def test_warm_club_reports_what_it_cached_when_it_gives_up() -> None:
    from apps.ladder_planner.tasks import _MAX_FETCH_ATTEMPTS as LIMIT
    from apps.ladder_planner.tasks import warm_club

    run = warm_club.func
    with (
        patch("apps.ladder_planner.tasks.get_club", side_effect=httpx.ReadTimeout("timed out")),
        patch("apps.ladder_planner.tasks.warm_club") as task,
    ):
        result = run(club_id=20650, _accumulated=900, _attempt=LIMIT - 1)

    assert result == {"status": "failed", "club_id": 20650, "cached": 900}
    task.using.return_value.enqueue.assert_not_called()
