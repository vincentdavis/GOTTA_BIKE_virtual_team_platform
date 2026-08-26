"""Manage Squads flags squads whose roles Role Setup no longer allows.

A squad keeps whatever role was stored before an event's allowed lists were narrowed.
SquadForm drops such a value from `initial` rather than offering it back -- which keeps
the form saveable, but means the role silently disappears the next time anyone edits that
squad. Surfacing it here is what makes the mismatch visible before that happens.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad
from apps.team.models import DiscordRole

ALLOWED_CPT, STRAY_CPT, REGION, HEAD = "601", "602", "610", "700"


@pytest.fixture
def event(db) -> Event:
    """Build an event allowing exactly one captain role and one region role.

    Returns:
        The event.

    """
    DiscordRole.objects.create(role_id=ALLOWED_CPT, name="$ Div 1 Captain", position=1)
    DiscordRole.objects.create(role_id=STRAY_CPT, name="$ Div 2 Captain", position=2)
    DiscordRole.objects.create(role_id=REGION, name="$ EMEA", position=3)
    DiscordRole.objects.create(role_id=HEAD, name="$ Head Captain", position=4)
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True,
        prefixes=["$"], head_captain_role_id=int(HEAD),
        captain_role_ids=[ALLOWED_CPT], region_role_ids=[REGION],
    )


def _page(client, event):
    """Load Manage Squads.

    Returns:
        The decoded body.

    """
    response = client.get(reverse("events:squad_manage", args=[event.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_a_clean_squad_is_not_flagged(client, event, superuser) -> None:
    """Only real mismatches should draw a red border."""
    Squad.objects.create(
        event=event, name="Good",
        discord_captain_role=int(ALLOWED_CPT), region_role=int(REGION),
    )
    client.force_login(superuser)

    body = _page(client, event)

    assert "border-2 border-error" not in body
    assert "Roles not allowed by Role Setup" not in body


@pytest.mark.django_db
def test_a_captain_role_off_the_allowed_list_is_flagged(client, event, superuser) -> None:
    """The case Vincent hit: seeded lists narrowed after squads were configured."""
    Squad.objects.create(event=event, name="Stray", discord_captain_role=int(STRAY_CPT))
    client.force_login(superuser)

    body = _page(client, event)

    assert "border-2 border-error" in body
    assert "not allowed by Role Setup" in body
    assert "@$ Div 2 Captain" in body


@pytest.mark.django_db
def test_a_region_role_off_the_allowed_list_is_flagged(client, event, superuser) -> None:
    """Same rule for the region list."""
    Squad.objects.create(event=event, name="Stray region", region_role=int(STRAY_CPT))
    client.force_login(superuser)

    assert "border-2 border-error" in _page(client, event)


@pytest.mark.django_db
def test_the_head_captain_role_gets_its_own_wording(client, event, superuser) -> None:
    """Holding it is a privilege problem, not a stale-list one, so it reads differently."""
    Squad.objects.create(event=event, name="Escalated", discord_captain_role=int(HEAD))
    client.force_login(superuser)

    body = _page(client, event)

    assert "Head Captain role" in body


@pytest.mark.django_db
def test_a_role_set_while_the_list_is_empty_is_flagged(client, event, superuser) -> None:
    """"Allows none" is a different sentence from "not on the list"."""
    event.captain_role_ids = []
    event.save(update_fields=["captain_role_ids"])
    Squad.objects.create(event=event, name="Orphan", discord_captain_role=int(ALLOWED_CPT))
    client.force_login(superuser)

    body = _page(client, event)

    assert "Role Setup allows none" in body


@pytest.mark.django_db
def test_a_squad_role_off_the_event_prefixes_is_flagged(client, event, superuser) -> None:
    """The squad role has no allow-list of its own, only the prefix rule."""
    DiscordRole.objects.create(role_id="900", name="/OFF Prefix", position=9)
    Squad.objects.create(event=event, name="Off prefix", team_discord_role=900)
    client.force_login(superuser)

    body = _page(client, event)

    assert "does not match the event&#x27;s prefixes" in body or "does not match the event's prefixes" in body
