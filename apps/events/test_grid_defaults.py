"""Event-level defaults and enforcement for the availability builder's toggles.

Setting a default and enforcing it are separate: a default seeds a new grid and can be
changed, an enforced default is locked. The builder disables the control, but it posts
JSON -- so every test here goes through the save endpoint, which is the only place
enforcement can actually hold.
"""

import json
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events import grid_defaults
from apps.events.models import AvailabilityGrid, Event, Squad


@pytest.fixture
def squad(db) -> Squad:
    """Build a squad on a visible event.

    Returns:
        The squad.

    """
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True,
    )
    return Squad.objects.create(event=event, name="Synthesis")


def _save(client, squad, **overrides):
    """Save a grid through the builder endpoint.

    Returns:
        The response.

    """
    payload = {
        "start_date": "2026-09-01",
        "end_date": "2026-09-02",
        "start_time": "19:00",
        "end_time": "21:00",
        "slot_duration": 60,
        "timezone": "UTC",
        "blocked_cells": [],
        **overrides,
    }
    return client.post(
        reverse("events:availability_create", args=[squad.event.pk, squad.pk]),
        data=json.dumps(payload),
        content_type="application/json",
    )


@pytest.mark.parametrize("setting", grid_defaults.SETTINGS)
def test_no_default_means_not_enforced(setting) -> None:
    """An enforce flag with nothing to enforce is meaningless, not a lock to False."""
    event = Event(**{f"grid_enforce_{setting}": True, f"grid_default_{setting}": None})
    assert grid_defaults.is_enforced(event, setting) is False
    assert grid_defaults.resolve(event, setting, submitted=True) is True


@pytest.mark.parametrize("setting", grid_defaults.SETTINGS)
def test_an_unenforced_default_does_not_override_the_captain(setting) -> None:
    event = Event(**{f"grid_default_{setting}": True, f"grid_enforce_{setting}": False})
    assert grid_defaults.resolve(event, setting, submitted=False) is False


@pytest.mark.parametrize("setting", grid_defaults.SETTINGS)
def test_enforcement_locks_in_both_directions(setting) -> None:
    """The point of this over the existing race-verified floor: it can force off too."""
    on = Event(**{f"grid_default_{setting}": True, f"grid_enforce_{setting}": True})
    off = Event(**{f"grid_default_{setting}": False, f"grid_enforce_{setting}": True})
    assert grid_defaults.resolve(on, setting, submitted=False) is True
    assert grid_defaults.resolve(off, setting, submitted=True) is False


@pytest.mark.django_db
def test_a_crafted_post_cannot_defeat_enforcement(client, squad, event_admin) -> None:
    """The disabled checkbox is a courtesy; this endpoint takes JSON from anyone."""
    Event.objects.filter(pk=squad.event.pk).update(
        grid_default_hide_empty_days=False, grid_enforce_hide_empty_days=True,
        grid_default_max_races_question=True, grid_enforce_max_races_question=True,
    )
    client.force_login(event_admin)

    _save(client, squad, hide_empty_days=True, max_races_question=False)

    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.hide_empty_days is False      # forced off despite the payload
    assert grid.max_races_question is True    # forced on despite the payload


@pytest.mark.django_db
def test_enforced_single_slot_still_shapes_the_grid(client, squad, event_admin) -> None:
    """single_slot decides whether the end date/time are derived.

    It is resolved before that derivation, so enforcing it has to produce a one-cell
    grid -- not a week-long one with the flag set.
    """
    Event.objects.filter(pk=squad.event.pk).update(
        grid_default_single_slot=True, grid_enforce_single_slot=True,
    )
    client.force_login(event_admin)

    _save(client, squad, single_slot=False, start_time="19:00", slot_duration=60)

    grid = AvailabilityGrid.objects.get(squad=squad)
    assert grid.single_slot is True
    assert grid.start_date == grid.end_date
    assert grid.end_time == "20:00"


@pytest.mark.django_db
def test_the_builder_seeds_a_new_grid_from_the_defaults(client, squad, event_admin) -> None:
    Event.objects.filter(pk=squad.event.pk).update(
        grid_default_expanded_features=True, grid_enforce_expanded_features=False,
    )
    client.force_login(event_admin)

    body = client.get(
        reverse("events:availability_create", args=[squad.event.pk, squad.pk])
    ).content.decode()

    assert '"expanded_features": true' in body.replace("&quot;", '"')
    assert 'id="grid-event-defaults"' in body
    assert 'id="grid-enforced"' in body


@pytest.mark.django_db
def test_save_buttons_sit_under_generate_and_stay_gated(client, squad, event_admin) -> None:
    """Save Grid and Save as Template moved out of the bulk-actions row.

    They keep the same guard they had there -- hidden until a grid exists -- so the
    move is layout only. Both breakpoints get a copy, mirroring the Generate buttons.
    """
    client.force_login(event_admin)

    body = client.get(
        reverse("events:availability_create", args=[squad.event.pk, squad.pk])
    ).content.decode()

    for button_id in ("btn-save", "btn-save-template", "btn-save-mobile", "btn-save-template-mobile"):
        assert f'id="{button_id}"' in body, button_id
    # Rendered hidden; buildGrid reveals them.
    assert body.count("save-action hidden") == 4
    # The bulk row keeps only the cell-editing actions.
    bulk = body.split('id="bulk-actions"')[1].split("</div>")[0]
    assert "Block All" in bulk
    assert "Save Grid" not in bulk
    assert "Save as Template" not in bulk
