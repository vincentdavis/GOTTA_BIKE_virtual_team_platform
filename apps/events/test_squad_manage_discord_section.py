"""The Discord section on the Manage Squads page."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad
from apps.team.models import DiscordRole

SQUAD_ROLE = "801"
CAPTAIN_ROLE = "802"


@pytest.fixture
def event(db) -> Event:
    """Build an event with the squad and captain roles nominated.

    Returns:
        The event.

    """
    DiscordRole.objects.create(role_id=SQUAD_ROLE, name="$ Div 1", position=1)
    DiscordRole.objects.create(role_id=CAPTAIN_ROLE, name="$ Div 1 Captain", position=2)
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True,
        prefixes=["$"], captain_role_ids=[CAPTAIN_ROLE],
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad carrying both roles.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Div 1",
        team_discord_role=int(SQUAD_ROLE), discord_captain_role=int(CAPTAIN_ROLE),
    )


def _page(client, event):
    """Load the Manage Squads page.

    Returns:
        The decoded body.

    """
    response = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_the_captain_role_is_shown_by_name(client, event, squad, superuser) -> None:
    """It was missing entirely: the view never collected its id, so no name resolved."""
    client.force_login(superuser)

    body = _page(client, event)

    assert "@$ Div 1 Captain" in body


@pytest.mark.django_db
def test_the_squad_role_is_still_shown(client, event, squad, superuser) -> None:
    """Adding the captain row must not have displaced the one beside it."""
    client.force_login(superuser)

    assert "@$ Div 1" in _page(client, event)


@pytest.mark.django_db
def test_the_discord_section_is_collapsed_by_default(client, event, squad, superuser) -> None:
    """A fully configured squad lists a channel, an audio channel and four roles.

    It also hosts the channel-access audit, which makes a live Discord call -- keeping
    that behind a click means loading this page does not.
    """
    client.force_login(superuser)
    body = _page(client, event)

    marker = '<details class="collapse collapse-arrow bg-base-300 mb-2">'
    assert marker in body
    # No `open` attribute -> closed on load.
    assert marker.replace(">", " open>") not in body


@pytest.mark.django_db
def test_a_squad_with_a_captain_role_but_nothing_else_still_shows_the_section(
    client, event, superuser
) -> None:
    """The section's visibility test has to include the captain role, or it hides itself."""
    Squad.objects.create(event=event, name="Captain only", discord_captain_role=int(CAPTAIN_ROLE))
    client.force_login(superuser)

    assert "@$ Div 1 Captain" in _page(client, event)
