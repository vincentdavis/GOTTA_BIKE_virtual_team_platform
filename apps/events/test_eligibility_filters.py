"""Ordering and filtering on the squad eligibility page.

The page exists to surface riders who cannot race, so those riders lead the list. That is
decided by ``User.is_race_ready``, not by the day count: ``race_ready_days`` is None both
for a rider with nothing to expire *and* for one whose records never expire, so sorting on
it buried the riders the page is for.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, EventSignup


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL Season 5", start_date=today, end_date=today + timedelta(days=30), visible=True
    )


def _rider(user_model, event, username, *, race_ready):
    """Register a rider with a known eligibility state.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test", first_name=username.title(), last_name="R",
    )
    user.is_race_ready = race_ready
    user.save(update_fields=["is_race_ready"])
    EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)
    return user


def _rows(client, event, **params):
    """Load the eligibility page and return the rider rows in display order.

    Returns:
        List of usernames in the order the page lists them.

    """
    response = client.get(reverse("events:squad_v_report", args=[event.pk]), params)
    assert response.status_code == 200
    body = response.content.decode()
    seen = []
    for name in ("Alpha R", "Bravo R", "Charlie R"):
        idx = body.find(name)
        if idx != -1:
            seen.append((idx, name))
    return [name for _, name in sorted(seen)]


@pytest.mark.django_db
def test_riders_without_a_current_verification_are_listed_first(client, event, event_admin, user_model) -> None:
    """They are the reason to open this page; they used to sort to the bottom."""
    _rider(user_model, event, "alpha", race_ready=True)
    _rider(user_model, event, "bravo", race_ready=False)
    client.force_login(event_admin)

    assert _rows(client, event)[0] == "Bravo R"


@pytest.mark.django_db
def test_filtering_to_not_eligible_drops_the_verified_riders(client, event, event_admin, user_model) -> None:
    """The filter has to key off the same signal as the ordering."""
    _rider(user_model, event, "alpha", race_ready=True)
    _rider(user_model, event, "bravo", race_ready=False)
    client.force_login(event_admin)

    assert _rows(client, event, eligible="no") == ["Bravo R"]


@pytest.mark.django_db
def test_filtering_to_eligible_drops_the_unverified_riders(client, event, event_admin, user_model) -> None:
    """The other direction, for picking a squad from who can actually race."""
    _rider(user_model, event, "alpha", race_ready=True)
    _rider(user_model, event, "bravo", race_ready=False)
    client.force_login(event_admin)

    assert _rows(client, event, eligible="yes") == ["Alpha R"]


@pytest.mark.django_db
def test_an_unrecognised_filter_value_shows_everyone(client, event, event_admin, user_model) -> None:
    """A hand-edited querystring must not silently hide riders."""
    _rider(user_model, event, "alpha", race_ready=True)
    _rider(user_model, event, "bravo", race_ready=False)
    client.force_login(event_admin)

    assert len(_rows(client, event, eligible="maybe")) == 2


@pytest.mark.django_db
def test_the_not_eligible_count_ignores_the_current_filter(client, event, event_admin, user_model) -> None:
    """Counted before filtering, so it stays a stable "this many need attention"."""
    _rider(user_model, event, "alpha", race_ready=True)
    _rider(user_model, event, "bravo", race_ready=False)
    client.force_login(event_admin)

    body = client.get(
        reverse("events:squad_v_report", args=[event.pk]), {"eligible": "yes"}
    ).content.decode()

    assert "1 not eligible" in body


@pytest.mark.django_db
def test_no_badge_when_every_rider_is_verified(client, event, event_admin, user_model) -> None:
    """A "0 not eligible" badge is noise."""
    _rider(user_model, event, "alpha", race_ready=True)
    client.force_login(event_admin)

    body = client.get(reverse("events:squad_v_report", args=[event.pk])).content.decode()

    assert "not eligible" not in body
