"""The event detail header's gear menu, which replaced the "Edit Event" button."""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible, currently-running event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
    )


def _detail(client, event: Event) -> str:
    """Fetch the event detail page body.

    Returns:
        The decoded response body.

    """
    return client.get(reverse("events:event_detail", args=[event.pk])).content.decode()


@pytest.mark.django_db
def test_event_admin_gets_both_menu_items_enabled(client, event, event_admin) -> None:
    """An event admin can reach event setup and squad manage, so neither is greyed out."""
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(event_admin)

    body = _detail(client, event)

    assert 'aria-label="Event actions"' in body
    assert "Event setup" in body
    assert "Manage squads" in body
    assert reverse("events:event_edit", args=[event.pk]) in body
    assert reverse("events:squad_manage", args=[event.pk]) in body
    assert "menu-disabled" not in body
    # The old always-visible button is gone.
    assert ">Edit Event<" not in body


@pytest.mark.django_db
def test_squad_captain_sees_event_setup_greyed_out(client, event, user_model) -> None:
    """A captain can manage squads but not edit the event, so one item is disabled."""
    squad = Squad.objects.create(event=event, name="Squad A")
    captain = user_model.objects.create_user(
        username="cap", email="cap@example.test",
        permission_overrides={"team_member": True},
    )
    squad.captains.add(captain)
    client.force_login(captain)

    body = _detail(client, event)

    assert 'aria-label="Event actions"' in body
    assert body.count("menu-disabled") == 1
    assert "Requires the event admin permission" in body
    # Listed but not actionable, so no href that would 403.
    assert "Event setup" in body
    assert reverse("events:event_edit", args=[event.pk]) not in body
    assert reverse("events:squad_manage", args=[event.pk]) in body


@pytest.mark.django_db
def test_plain_member_gets_no_gear_at_all(client, event, team_member) -> None:
    """With neither permission there is nothing in the menu, so it is not rendered."""
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(team_member)

    body = _detail(client, event)

    assert 'aria-label="Event actions"' not in body
    assert "Event setup" not in body
    assert reverse("events:event_edit", args=[event.pk]) not in body
