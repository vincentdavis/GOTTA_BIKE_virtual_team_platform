"""Expired session rows are cleared on a schedule.

Sessions are database-backed and an expired row keeps `_auth_user_id` in its payload, so
it is personal data held past its purpose. Nothing had ever deleted them -- `clearsessions`
appeared nowhere in the repo.
"""

from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.accounts.tasks import clear_expired_sessions
from gotta_bike_platform.task_registry import TASK_REGISTRY, resolve_interval_minutes


def _session(*, expires_in_days: int) -> Session:
    """Create a session row expiring relative to now.

    Returns:
        The session.

    """
    from django.contrib.sessions.backends.db import SessionStore

    store = SessionStore()
    store["_auth_user_id"] = "1"
    store.set_expiry(timezone.now() + timedelta(days=expires_in_days))
    store.create()
    return Session.objects.get(session_key=store.session_key)


@pytest.mark.django_db
def test_an_expired_session_is_deleted() -> None:
    """The point: the row and the user id it carries go away."""
    stale = _session(expires_in_days=-1)

    result = clear_expired_sessions.func()

    assert not Session.objects.filter(session_key=stale.session_key).exists()
    assert result["deleted"] == 1


@pytest.mark.django_db
def test_a_live_session_is_left_alone() -> None:
    """Clearing an active session would log somebody out mid-use."""
    live = _session(expires_in_days=7)

    clear_expired_sessions.func()

    assert Session.objects.filter(session_key=live.session_key).exists()


@pytest.mark.django_db
def test_the_count_reports_the_backlog() -> None:
    """Nothing has ever cleared these, so the first run drains a backlog worth seeing."""
    for _ in range(3):
        _session(expires_in_days=-2)
    _session(expires_in_days=3)

    assert clear_expired_sessions.func()["deleted"] == 3


@pytest.mark.django_db
def test_it_is_registered_to_run_every_48_hours() -> None:
    """A cleanup nobody scheduled is the state this replaced."""
    entry = TASK_REGISTRY["clear_expired_sessions"]

    assert entry["scheduled"] is True
    assert entry["hours_setting"] == "SCHEDULER_CLEAR_SESSIONS_HOURS"
    assert resolve_interval_minutes(entry) == 48 * 60
