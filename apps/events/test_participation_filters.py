"""Timezone and gender on the participation report.

Both are properties of the squad, not the rider, so they sit on the squad heading and
the filters hide whole squad blocks -- the same shape as the Manage Squads page.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad, SquadMember


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True,
    )


def _squad(event, name, tz="", gender=""):
    """Build a squad with a profile.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name=name, squad_timezone=tz, gender=gender)


def _rider(user_model, squad, username):
    """Put a rider in a squad so the block renders.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(username=username, email=f"{username}@example.test")
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


@pytest.mark.django_db
def test_the_squad_heading_shows_timezone_and_gender(client, event, event_admin, user_model) -> None:
    squad = _squad(event, "Alpha", tz="Europe/London", gender="Female")
    _rider(user_model, squad, "r1")
    client.force_login(event_admin)

    body = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation").content.decode()
    heading = body[body.index(">Alpha<"):body.index(">Alpha<") + 400]

    assert "Europe/London" in heading
    assert "Female" in heading


@pytest.mark.django_db
def test_the_filters_offer_only_values_in_use(client, event, event_admin, user_model) -> None:
    """Offering a value that matches nothing just produces an empty page."""
    alpha = _squad(event, "Alpha", tz="Europe/London", gender="Female")
    bravo = _squad(event, "Bravo", tz="US/Mountain")
    _rider(user_model, alpha, "r1")
    _rider(user_model, bravo, "r2")
    client.force_login(event_admin)

    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    assert resp.context["participation_timezones"] == ["Europe/London", "US/Mountain"]
    assert resp.context["participation_genders"] == ["Female"]     # Bravo has none


@pytest.mark.django_db
def test_the_filter_bar_is_dropped_when_no_squad_has_a_profile(client, event, event_admin, user_model) -> None:
    """Two "All …" selects that can never narrow anything are just clutter."""
    _rider(user_model, _squad(event, "Alpha"), "r1")
    client.force_login(event_admin)

    resp = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    assert resp.context["participation_timezones"] == []
    assert resp.context["participation_genders"] == []
    assert 'id="filter-participation-timezone"' not in resp.content.decode()


@pytest.mark.django_db
def test_each_block_carries_the_data_the_filter_matches_on(client, event, event_admin, user_model) -> None:
    """The filtering is client-side, so the values have to reach the markup."""
    alpha = _squad(event, "Alpha", tz="Europe/London", gender="Female")
    _rider(user_model, alpha, "r1")
    client.force_login(event_admin)

    body = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation").content.decode()

    assert 'class="mb-6 participation-squad"' in body
    assert 'data-timezone="Europe/London"' in body
    assert 'data-gender="Female"' in body
