"""Optional "Expand Features" context on an availability grid.

A captain can attach a markdown description and four links to a grid. The toggle is
stored rather than inferred from "are any of these filled in", so turning the panel off
hides the content without destroying it.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import AvailabilityGrid, Event, Squad, SquadMember

URL_FIELDS = ("website_url", "course_url", "recon_url", "invite_url")


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


@pytest.fixture
def grid(squad) -> AvailabilityGrid:
    """Build a published grid carrying the expanded context.

    Returns:
        The grid.

    """
    today = date.today()
    return AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today,
        end_date=today + timedelta(days=6),
        start_time="17:00",
        end_time="21:00",
        slot_duration=30,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
        expanded_features=True,
        description="Bring a **fast** bike.\n\n- Lap 1 is neutral",
        website_url="https://example.test/event",
        course_url="https://example.test/course",
        recon_url="https://example.test/recon",
        invite_url="https://example.test/invite",
    )


@pytest.fixture
def rider(user_model, squad):
    """Add a squad member who can fill in the grid.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username="rider", email="rider@example.test",
        permission_overrides={"team_member": True},
    )
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _body(client, grid) -> str:
    """Fetch the rider-facing respond page.

    Returns:
        The rendered HTML.

    """
    return client.get(
        reverse("events:availability_respond", args=[grid.squad.event.pk, grid.squad.pk, grid.id])
    ).content.decode()


@pytest.mark.django_db
def test_defaults_are_off_and_empty(squad) -> None:
    """An existing grid must not sprout a panel just because the fields exist."""
    today = date.today()
    plain = AvailabilityGrid.objects.create(
        squad=squad, start_date=today, end_date=today, start_time="17:00", end_time="18:00",
        slot_duration=30, grid_timezone="UTC",
    )
    assert plain.expanded_features is False
    assert plain.description == ""
    assert all(getattr(plain, f) == "" for f in URL_FIELDS)


@pytest.mark.django_db
def test_riders_see_the_description_rendered_as_markdown(client, grid, rider) -> None:
    client.force_login(rider)

    body = _body(client, grid)

    assert "<strong>fast</strong>" in body
    assert "<li>Lap 1 is neutral</li>" in body


@pytest.mark.django_db
def test_riders_see_every_link(client, grid, rider) -> None:
    client.force_login(rider)

    body = _body(client, grid)

    for field in URL_FIELDS:
        assert getattr(grid, field) in body, field
    assert 'rel="noopener"' in body


@pytest.mark.django_db
def test_unticking_hides_the_panel_without_losing_the_content(client, grid, rider) -> None:
    """The reason the toggle is stored rather than inferred from emptiness."""
    grid.expanded_features = False
    grid.save(update_fields=["expanded_features"])
    client.force_login(rider)

    body = _body(client, grid)

    assert "https://example.test/course" not in body
    grid.refresh_from_db()
    assert grid.description.startswith("Bring a")
    assert grid.course_url == "https://example.test/course"


@pytest.mark.django_db
def test_the_builder_renders_the_toggle_and_inputs(client, squad, event_admin) -> None:
    client.force_login(event_admin)

    body = client.get(
        reverse("events:availability_create", args=[squad.event.pk, squad.pk])
    ).content.decode()

    assert 'id="cfg-expanded-features"' in body
    assert 'id="expanded-features-panel"' in body
    assert 'id="cfg-description"' in body
    for key in ("website", "course", "recon", "invite"):
        assert f'id="cfg-{key}-url"' in body, key


@pytest.mark.django_db
def test_the_builder_round_trips_the_fields(client, squad, event_admin) -> None:
    """Save then reopen: the serialise and parse halves have to agree.

    They are written in two separate places in views.py, so a field added to one and
    not the other saves fine and comes back blank on the next edit.
    """
    import json

    client.force_login(event_admin)
    today = date.today()
    payload = {
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=2)).isoformat(),
        "start_time": "17:00",
        "end_time": "19:00",
        "slot_duration": 30,
        "timezone": "UTC",
        "blocked_cells": [],
        "expanded_features": True,
        "description": "Bring a **fast** bike.",
        "website_url": "https://example.test/event",
        "course_url": "https://example.test/course",
        "recon_url": "https://example.test/recon",
        "invite_url": "https://example.test/invite",
    }

    resp = client.post(
        reverse("events:availability_create", args=[squad.event.pk, squad.pk]),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code in (200, 201), resp.content[:300]

    saved = AvailabilityGrid.objects.filter(squad=squad).latest("created_at")
    assert saved.expanded_features is True
    assert saved.description == "Bring a **fast** bike."
    for field in URL_FIELDS:
        assert getattr(saved, field) == payload[field], field

    # And the edit page must hand them back to the builder, or they vanish on re-save.
    body = client.get(
        reverse("events:availability_edit", args=[squad.event.pk, squad.pk, saved.id])
    ).content.decode()
    assert "https://example.test/recon" in body
    assert '"expanded_features": true' in body.lower().replace("'", '"')


@pytest.mark.django_db
def test_duplicating_a_grid_carries_the_context(client, grid, event_admin) -> None:
    """A copied grid keeps its description and links rather than starting blank."""
    client.force_login(event_admin)

    # The copy view takes the new date range in the POST; without it the copy is refused.
    new_start = date.today() + timedelta(days=14)
    client.post(
        reverse("events:availability_copy", args=[grid.squad.event.pk, grid.squad.pk, grid.id]),
        data={"start_date": new_start.isoformat(), "end_date": (new_start + timedelta(days=6)).isoformat()},
    )

    copy = AvailabilityGrid.objects.filter(squad=grid.squad).exclude(pk=grid.pk).latest("created_at")
    assert copy.expanded_features is True
    assert copy.description == grid.description
    assert copy.invite_url == grid.invite_url
    # hide_empty_days was being dropped by this path before; pin it so it stays fixed.
    assert copy.hide_empty_days == grid.hide_empty_days
