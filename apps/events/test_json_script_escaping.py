"""Rider- and captain-authored text must not break out of the availability pages' script blocks.

These pages shipped their data as ``json.dumps(...)|safe`` interpolated straight into
``<script>``. ``json.dumps`` does not escape ``<``, and an HTML parser ends a ``<script>``
at the first ``</script`` whatever the JS quoting -- so a rider whose surname was
``</script><img src=x onerror=...>`` got script execution in their squad captain's
session. Django's ``|json_script`` escapes ``<``, ``>`` and ``&``, which closes it.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events.models import AvailabilityGrid, Event, Squad, SquadMember

BREAKOUT = '</script><img src=x onerror=alert(1)>'


@pytest.fixture
def grid_setup(db, team_member):
    """Build an event/squad/grid whose captain is `team_member`.

    Returns:
        An ``(event, squad, grid)`` tuple.

    """
    today = timezone.now().date()
    event = Event.objects.create(
        title="Series", start_date=today - timedelta(days=1), end_date=today + timedelta(days=7), visible=True,
    )
    squad = Squad.objects.create(event=event, name="Alpha")
    squad.captains.add(team_member)
    grid = AvailabilityGrid.objects.create(
        squad=squad,
        start_date=today,
        end_date=today + timedelta(days=1),
        start_time="18:00",
        end_time="20:00",
        slot_duration=30,
        grid_timezone="UTC",
        status=AvailabilityGrid.Status.PUBLISHED,
    )
    return event, squad, grid


def _assert_no_breakout(body: str) -> None:
    """Fail if the payload's "<" reached the page unescaped.

    The attack is entirely about "<": inside the JSON string `onerror=alert(1)` is
    inert text, and it stays inert as long as no "<" survives to start a tag.
    """
    assert "</script><img" not in body
    assert "<img src=x" not in body
    # Present, but encoded -- proves the payload was escaped rather than silently dropped,
    # so this test would still fail if the data stopped reaching the page at all.
    assert "\\u003C/script\\u003E\\u003Cimg" in body


@pytest.mark.django_db
def test_a_riders_name_cannot_close_the_results_script_block(client, grid_setup, user_model, team_member) -> None:
    """The consequential one: any rider picks their own name, the captain reads the page."""
    event, squad, grid = grid_setup
    rider = user_model.objects.create(username="xss", first_name="Bob", last_name=BREAKOUT, zwid=9001)
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    response = client.get(reverse("events:availability_results", args=[event.pk, squad.pk, grid.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    # The name really did reach the page's data, so the escaping is what is being tested.
    assert rider.pk in response.context["user_data_json"]
    _assert_no_breakout(body)


@pytest.mark.django_db
def test_a_grid_description_cannot_close_the_builder_script_block(client, grid_setup, team_member) -> None:
    """The edit page ships the grid back to the captain as JSON, description included."""
    event, squad, grid = grid_setup
    grid.description = BREAKOUT
    grid.expanded_features = True
    grid.save(update_fields=["description", "expanded_features"])
    client.force_login(team_member)

    response = client.get(reverse("events:availability_edit", args=[event.pk, squad.pk, grid.pk]))

    assert response.status_code == 200
    assert response.context["initial_grid_json"]["description"] == BREAKOUT
    _assert_no_breakout(response.content.decode())


@pytest.mark.django_db
def test_the_create_page_still_has_no_initial_grid(client, grid_setup, team_member) -> None:
    """The builder serves both create and edit; only edit ships an initial grid."""
    event, squad, _ = grid_setup
    client.force_login(team_member)

    response = client.get(reverse("events:availability_create", args=[event.pk, squad.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="initial-grid"' not in body
    # The JS must cope with the element being absent rather than assuming null was inlined.
    assert "initialGridEl ? JSON.parse(initialGridEl.textContent) : null" in body


@pytest.mark.django_db
def test_the_respond_page_ships_its_grid_data_as_json_script(client, grid_setup, user_model) -> None:
    """The rider-facing page carries no free text, but must not keep the old pattern either."""
    event, squad, grid = grid_setup
    rider = user_model.objects.create_user(
        username="responder", email="r@example.test", permission_overrides={"team_member": True},
    )
    SquadMember.objects.create(squad=squad, user=rider, status=SquadMember.Status.MEMBER)
    client.force_login(rider)

    response = client.get(reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    for element_id in ("display-dates", "display-time-slots", "display-blocked",
                       "existing-local-keys", "cell-utc-map", "valid-cells"):
        assert f'id="{element_id}"' in body, element_id
        assert f"document.getElementById('{element_id}').textContent" in body, element_id
