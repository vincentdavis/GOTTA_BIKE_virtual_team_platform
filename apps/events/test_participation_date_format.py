"""Race dates on the participation report are compact.

Two badges share a narrow cell, so "Sep 22, 2026" was most of the column width. The year
is redundant inside a single event, and the full date stays available on hover.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import (
    AvailabilityGrid,
    AvailabilitySlotSelection,
    Event,
    EventSignup,
    Squad,
    SquadMember,
)


@pytest.fixture
def scheduled(db, user_model):
    """Build an event with one scheduled race on a single-digit day.

    Returns:
        The event.

    """
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=90), visible=True
    )
    squad = Squad.objects.create(event=event, name="Div 1")
    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test", first_name="Ana", last_name="Rider",
    )
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)

    grid = AvailabilityGrid.objects.create(
        squad=squad, start_date=today, end_date=today + timedelta(days=7),
        start_time="18:00", end_time="20:00", slot_duration=60,
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    selection = AvailabilitySlotSelection.objects.create(
        grid=grid, name="Race 1",
        slot_date=date(2026, 9, 2), slot_time="17:30",
    )
    selection.selected_users.add(rider)
    return event


@pytest.mark.django_db
def test_the_date_is_month_and_day_only(auth_client, scheduled) -> None:
    """The year and any padding are what made the column wide."""
    body = auth_client.get(
        reverse("events:event_all_races", args=[scheduled.pk]), {"tab": "participation"}
    ).content.decode()

    # The visible badge text, not the tooltip -- the tooltip keeps the year on purpose.
    assert ">SEP 2 &middot; Race 1</span>" in body
    assert "Sep 02" not in body  # %d pads; the day is formatted separately to avoid it
    assert "Sep 2, 2026" not in body


@pytest.mark.django_db
def test_the_full_date_is_still_available_on_hover(auth_client, scheduled) -> None:
    """Compact should not mean lossy -- dropping the year needs somewhere to put it back."""
    body = auth_client.get(
        reverse("events:event_all_races", args=[scheduled.pk]), {"tab": "participation"}
    ).content.decode()

    assert "September 2026" in body
