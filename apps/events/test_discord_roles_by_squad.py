"""The "By Squad" tab on the Discord Roles page.

The riders matrix can already show a rider holding a squad's role without being in that
squad, but only if you read a wide grid cell by cell. This tab flips the axes and states
the drift outright, because that drift is channel access nobody granted on purpose.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad, SquadMember

SQUAD_ROLE = 9001
CAPTAIN_ROLE = 9002


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a team role and a captain role configured.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Div 1", team_discord_role=SQUAD_ROLE, discord_captain_role=CAPTAIN_ROLE
    )


def _member(user_model, squad, username, *, roles=None, joined=True):
    """Create a user, optionally in the squad and optionally holding Discord roles.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test",
        first_name=username.title(), last_name="R",
        discord_id=f"d{username}", discord_roles=roles or {},
    )
    if joined:
        SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _page(client, event, tab="squads"):
    """Load the Discord Roles page on a given tab.

    Returns:
        The decoded body.

    """
    response = client.get(reverse("events:discord_roles", args=[event.pk]), {"tab": tab})
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_a_role_holder_who_is_not_in_the_squad_is_named(client, event, squad, superuser, user_model) -> None:
    """The whole point of the tab: they can see the squad's channel without being on it."""
    _member(user_model, squad, "intruder", roles={str(SQUAD_ROLE): "Div 1"}, joined=False)
    client.force_login(superuser)

    body = _page(client, event)

    assert "not in squad" in body
    assert "Intruder R" in body


@pytest.mark.django_db
def test_a_member_holding_the_role_is_not_flagged(client, event, squad, superuser, user_model) -> None:
    """Holding your own squad's role is the correct state, not drift."""
    _member(user_model, squad, "regular", roles={str(SQUAD_ROLE): "Div 1"})
    client.force_login(superuser)

    body = _page(client, event)

    assert "not in squad" not in body


@pytest.mark.django_db
def test_a_member_without_the_role_counts_as_missing(client, event, squad, superuser, user_model) -> None:
    """The mirror case -- they cannot see their own squad's channel."""
    _member(user_model, squad, "roleless")
    client.force_login(superuser)

    body = _page(client, event)

    assert "1 missing" in body


@pytest.mark.django_db
def test_captain_role_is_measured_against_captains_not_members(client, event, squad, superuser, user_model) -> None:
    """A plain member holding the captain role is drift; a captain holding it is not."""
    plain = _member(user_model, squad, "plain", roles={str(CAPTAIN_ROLE): "Div 1 Captain"})
    skipper = _member(user_model, squad, "skipper", roles={str(CAPTAIN_ROLE): "Div 1 Captain"})
    squad.captains.add(skipper)
    client.force_login(superuser)

    body = _page(client, event)

    assert "Plain R" in body
    assert plain.pk != skipper.pk


@pytest.mark.django_db
def test_the_regional_coordinator_role_is_not_audited(client, event, superuser, user_model) -> None:
    """Coordinating is a job, not a consequence of squad membership.

    Measuring it against the roster would report every coordinator as drift, which is the
    same reasoning the stragglers block uses.
    """
    coord_squad = Squad.objects.create(event=event, name="Div 2", regional_coordinator_role=9003)
    _member(user_model, coord_squad, "coord", roles={"9003": "EMEA Coordinator"}, joined=False)
    client.force_login(superuser)

    body = _page(client, event)

    assert "Coord R" not in body


@pytest.mark.django_db
def test_the_tab_is_read_only(client, event, squad, superuser, user_model) -> None:
    """It exists to find problems; fixing them stays on the Riders tab."""
    _member(user_model, squad, "intruder", roles={str(SQUAD_ROLE): "Div 1"}, joined=False)
    client.force_login(superuser)

    body = _page(client, event)

    assert "toggle-captain-role" not in body
    assert "toggle-role" not in body


@pytest.mark.django_db
def test_the_riders_tab_still_renders_by_default(client, event, squad, superuser) -> None:
    """Adding a tab must not have hidden the page's original content."""
    client.force_login(superuser)

    response = client.get(reverse("events:discord_roles", args=[event.pk]))

    assert response.status_code == 200
    assert "data-roles-table" in response.content.decode()


@pytest.mark.django_db
def test_the_tab_carries_a_problem_count(client, event, squad, superuser, user_model) -> None:
    """The badge is what makes the tab worth clicking when something is wrong."""
    _member(user_model, squad, "intruder", roles={str(SQUAD_ROLE): "Div 1"}, joined=False)
    client.force_login(superuser)

    body = _page(client, event, tab="riders")

    assert "By Squad" in body
    assert "badge-error" in body
