"""Availability grid templates shared across squads.

A shared template carries the times and timezone of the squad that built it, so the
borrowing squad is warned to check both. Applying one only copies grid configuration --
never rider data -- which is why a template from another squad is safe to offer at all.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import AvailabilityGrid, AvailabilityGridTemplate, Event, Squad


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


def _template(squad, name="Tuesday Nights", *, shared=False) -> AvailabilityGridTemplate:
    """Build a template owned by a squad.

    Returns:
        The template.

    """
    return AvailabilityGridTemplate.objects.create(
        squad=squad, name=name, start_time="19:00", end_time="21:00",
        grid_timezone="Europe/London", slot_duration=60, default_length_days=7,
        shared=shared,
    )


@pytest.mark.django_db
def test_a_shared_template_is_offered_to_another_squad(client, event, event_admin) -> None:
    owner = Squad.objects.create(event=event, name="Synthesis")
    borrower = Squad.objects.create(event=event, name="Catalyst")
    _template(owner, shared=True)
    client.force_login(event_admin)

    body = client.get(
        reverse("events:squad_availability", args=[event.pk, borrower.pk])
    ).content.decode()

    assert "Shared templates" in body
    assert "Tuesday Nights" in body
    assert "Synthesis" in body          # the source squad is named


@pytest.mark.django_db
def test_an_unshared_template_stays_private(client, event, event_admin) -> None:
    owner = Squad.objects.create(event=event, name="Synthesis")
    borrower = Squad.objects.create(event=event, name="Catalyst")
    _template(owner, name="Private Thing", shared=False)
    client.force_login(event_admin)

    body = client.get(
        reverse("events:squad_availability", args=[event.pk, borrower.pk])
    ).content.decode()

    assert "Private Thing" not in body


@pytest.mark.django_db
def test_a_squad_does_not_see_its_own_shared_template_twice(client, event, event_admin) -> None:
    """It already appears in the squad's own list; a second copy is just confusing."""
    owner = Squad.objects.create(event=event, name="Synthesis")
    _template(owner, shared=True)
    client.force_login(event_admin)

    body = client.get(
        reverse("events:squad_availability", args=[event.pk, owner.pk])
    ).content.decode()

    assert "Shared templates" not in body


@pytest.mark.django_db
def test_the_use_dialog_warns_about_time_and_timezone(client, event, event_admin) -> None:
    """The borrowed template keeps the other squad's clock, which is the whole risk."""
    owner = Squad.objects.create(event=event, name="Synthesis")
    borrower = Squad.objects.create(event=event, name="Catalyst")
    _template(owner, shared=True)
    client.force_login(event_admin)

    body = client.get(
        reverse("events:squad_availability", args=[event.pk, borrower.pk])
    ).content.decode()

    assert "alert-warning" in body
    assert "Check the time and timezone" in body
    assert "Europe/London" in body


@pytest.mark.django_db
def test_another_squad_can_actually_apply_it(client, event, event_admin) -> None:
    """The apply view was scoped to the squad's own templates; a shared one has to pass."""
    owner = Squad.objects.create(event=event, name="Synthesis")
    borrower = Squad.objects.create(event=event, name="Catalyst")
    template = _template(owner, shared=True)
    client.force_login(event_admin)

    client.post(
        reverse("events:availability_template_apply", args=[event.pk, borrower.pk, template.pk]),
        data={"start_date": "2026-09-01"},
    )

    grid = AvailabilityGrid.objects.get(squad=borrower)
    # Templates hold local time, grids hold UTC. 19:00 Europe/London on 1 September is
    # BST, so it lands at 18:00Z -- which is exactly why the dialog tells the borrowing
    # squad to check the clock.
    assert grid.start_time == "18:00"
    assert grid.grid_timezone == "Europe/London"
    assert grid.status == AvailabilityGrid.Status.DRAFT


@pytest.mark.django_db
def test_an_unshared_template_cannot_be_applied_by_another_squad(client, event, event_admin) -> None:
    owner = Squad.objects.create(event=event, name="Synthesis")
    borrower = Squad.objects.create(event=event, name="Catalyst")
    template = _template(owner, shared=False)
    client.force_login(event_admin)

    resp = client.post(
        reverse("events:availability_template_apply", args=[event.pk, borrower.pk, template.pk]),
        data={"start_date": "2026-09-01"},
    )

    assert resp.status_code == 404
    assert not AvailabilityGrid.objects.filter(squad=borrower).exists()
