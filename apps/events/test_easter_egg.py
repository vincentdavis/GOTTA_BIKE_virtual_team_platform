"""TEMPORARY -- delete alongside the easter egg in availability_respond.html.

Pins the one thing that would actually be a problem: the message must reach exactly one
rider and nobody else.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import AvailabilityGrid, Event, Squad, SquadMember

TARGET_DISCORD_ID = "1201456726373834752"
MESSAGE = "Like Sisyphus pushing his rock"


@pytest.fixture
def grid(db):
    """Build a published grid.

    Returns:
        The grid.

    """
    today = date.today()
    event = Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True
    )
    squad = Squad.objects.create(event=event, name="Div 1")
    return AvailabilityGrid.objects.create(
        squad=squad, start_date=today, end_date=today + timedelta(days=7),
        start_time="18:00", end_time="20:00", slot_duration=60,
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _visit(client, grid):
    """Load the availability page.

    Returns:
        The decoded body.

    """
    response = client.get(
        reverse("events:availability_respond",
                args=[grid.squad.event_id, grid.squad_id, str(grid.id)])
    )
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_the_target_rider_sees_it(client, grid, team_member) -> None:
    """The whole point."""
    team_member.discord_id = TARGET_DISCORD_ID
    team_member.save(update_fields=["discord_id"])
    SquadMember.objects.create(squad=grid.squad, user=team_member, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    assert MESSAGE in _visit(client, grid)


@pytest.mark.django_db
def test_nobody_else_does(client, grid, team_member) -> None:
    """A joke aimed at one person must not greet the whole squad."""
    team_member.discord_id = "999999999999999999"
    team_member.save(update_fields=["discord_id"])
    SquadMember.objects.create(squad=grid.squad, user=team_member, status=SquadMember.Status.MEMBER)
    client.force_login(team_member)

    assert MESSAGE not in _visit(client, grid)
