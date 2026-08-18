"""The event detail header: gear menu, and the icon-only Discord channel link."""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad

DISCORD_MARK_PATH_START = "M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515"


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
def test_event_admin_gets_every_menu_item_enabled(client, event, event_admin) -> None:
    """An event admin passes all three gates, so nothing is greyed out."""
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(event_admin)

    body = _detail(client, event)

    assert 'aria-label="Event actions"' in body
    for label in ("Event setup", "Manage squads", "Eligibility"):
        assert label in body
    assert reverse("events:event_edit", args=[event.pk]) in body
    assert reverse("events:squad_manage", args=[event.pk]) in body
    assert reverse("events:squad_v_report", args=[event.pk]) in body
    assert "menu-disabled" not in body
    # Each URL appears once: the old header button and the two Squads-section buttons are gone.
    assert body.count(reverse("events:squad_manage", args=[event.pk])) == 1
    assert body.count(reverse("events:squad_v_report", args=[event.pk])) == 1
    assert ">Edit Event<" not in body
    assert ">Manage Squads<" not in body


@pytest.mark.django_db
def test_squad_captain_sees_event_setup_greyed_out(client, event, user_model) -> None:
    """A captain can manage squads but not edit the event, so one item is disabled."""
    squad = Squad.objects.create(event=event, name="Squad A")
    captain = user_model.objects.create_user(
        username="cap",
        email="cap@example.test",
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
    # A squad captain also passes the eligibility gate.
    assert reverse("events:squad_v_report", args=[event.pk]) in body


@pytest.mark.django_db
def test_plain_member_gets_no_gear_at_all(client, event, team_member) -> None:
    """Failing all three gates leaves the menu empty, so it is not rendered at all."""
    Squad.objects.create(event=event, name="Squad A")
    client.force_login(team_member)

    body = _detail(client, event)

    assert 'aria-label="Event actions"' not in body
    assert "Event setup" not in body
    assert "Eligibility" not in body
    assert reverse("events:event_edit", args=[event.pk]) not in body
    assert reverse("events:squad_v_report", args=[event.pk]) not in body


@pytest.mark.django_db
def test_event_discord_link_is_the_brand_mark_not_text(client, event, team_member) -> None:
    """The event's Discord channel link under the description is icon-only."""
    event.discord_channel_id = 555
    event.url = "https://example.test/event"
    event.save(update_fields=["discord_channel_id", "url"])
    client.force_login(team_member)

    body = _detail(client, event)

    assert DISCORD_MARK_PATH_START in body
    assert 'data-tip="Discord channel"' in body
    assert 'aria-label="Discord channel"' in body
    assert not re.search(r">\s*Discord Channel\s*<", body)
    # The neighbouring Event Link keeps its text, since no icon says "event page".
    assert re.search(r">\s*Event Link", body)
