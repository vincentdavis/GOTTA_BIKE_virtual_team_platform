"""Warn when a squad's Discord channel is readable by the whole server.

Race threads inherit their parent channel's visibility, so a squad thread carrying rider
names, availability and selections is team-wide whenever its channel is. Discord's thread
type is not the lever -- the parent channel is.

Checked on every render rather than stored: channel permissions live in Discord and change
there, so a check made when the squad was set up would go stale with nobody noticing.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad
from apps.events.views import _public_channel_ids, _squad_channel_warnings

VIEW_CHANNEL = 1 << 10
GUILD_ID = "111111"
CHANNEL_ID = "555555"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the channel-lookup cache, which the view holds for a minute."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Channel Test",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
    )


def _roles(everyone_can_view: bool) -> list[dict]:
    return [{"id": GUILD_ID, "name": "@everyone", "permissions": str(VIEW_CHANNEL if everyone_can_view else 0)}]


def _channel(*, deny_everyone: bool) -> dict:
    overwrites = []
    if deny_everyone:
        overwrites = [{"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}]
    return {"id": CHANNEL_ID, "name": "squad-chat", "permission_overwrites": overwrites}


@pytest.mark.django_db
def test_a_channel_everyone_can_see_is_flagged(event, settings):
    with patch("apps.events.views.config") as cfg:
        cfg.GUILD_ID = GUILD_ID
        public = _public_channel_ids([_channel(deny_everyone=False)], _roles(everyone_can_view=True))

    squad = Squad(event=event, name="A", discord_channel_id=CHANNEL_ID)
    assert CHANNEL_ID in public
    assert _squad_channel_warnings(squad, public)


@pytest.mark.django_db
def test_a_channel_that_denies_everyone_is_clean(event):
    with patch("apps.events.views.config") as cfg:
        cfg.GUILD_ID = GUILD_ID
        public = _public_channel_ids([_channel(deny_everyone=True)], _roles(everyone_can_view=True))

    squad = Squad(event=event, name="A", discord_channel_id=CHANNEL_ID)
    assert CHANNEL_ID not in public
    assert _squad_channel_warnings(squad, public) == []


@pytest.mark.django_db
def test_discord_being_unreachable_is_not_the_same_as_private(event):
    """An outage must not silently clear every warning on the page."""
    assert _public_channel_ids(None, None) is None

    squad = Squad(event=event, name="A", discord_channel_id=CHANNEL_ID)
    assert _squad_channel_warnings(squad, None) == []


@pytest.mark.django_db
def test_a_squad_with_no_channel_is_never_flagged(event):
    squad = Squad(event=event, name="A", discord_channel_id="")
    assert _squad_channel_warnings(squad, {CHANNEL_ID}) == []


@pytest.mark.django_db
def test_the_page_outlines_the_squad_in_red(client, event, event_admin):
    Squad.objects.create(event=event, name="Open Squad", discord_channel_id=CHANNEL_ID)
    client.force_login(event_admin)

    with (
        patch("apps.accounts.discord_service.get_guild_channels", return_value=[_channel(deny_everyone=False)]),
        patch("apps.accounts.discord_service.get_guild_roles", return_value=_roles(everyone_can_view=True)),
        patch("apps.events.views.config") as cfg,
    ):
        cfg.GUILD_ID = GUILD_ID
        cfg.EVENT_ROLE_PREFIXES = []
        body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert "Discord channel is visible to the whole server" in body
    assert "border-2 border-error" in body


@pytest.mark.django_db
def test_a_private_channel_leaves_the_squad_unmarked(client, event, event_admin):
    Squad.objects.create(event=event, name="Closed Squad", discord_channel_id=CHANNEL_ID)
    client.force_login(event_admin)

    with (
        patch("apps.accounts.discord_service.get_guild_channels", return_value=[_channel(deny_everyone=True)]),
        patch("apps.accounts.discord_service.get_guild_roles", return_value=_roles(everyone_can_view=True)),
        patch("apps.events.views.config") as cfg,
    ):
        cfg.GUILD_ID = GUILD_ID
        cfg.EVENT_ROLE_PREFIXES = []
        body = client.get(reverse("events:squad_manage", args=[event.pk])).content.decode()

    assert "Discord channel is visible to the whole server" not in body


@pytest.mark.django_db
def test_discord_is_not_called_twice_in_a_minute(client, event, event_admin):
    """Two blocking calls per render, each with a 10s timeout, on a page captains live on.

    Without a cache a slow Discord turns this into a twenty-second page.
    """
    Squad.objects.create(event=event, name="A", discord_channel_id=CHANNEL_ID)
    client.force_login(event_admin)
    url = reverse("events:squad_manage", args=[event.pk])

    with (
        patch(
            "apps.accounts.discord_service.get_guild_channels",
            return_value=[_channel(deny_everyone=False)],
        ) as fetch,
        patch("apps.accounts.discord_service.get_guild_roles", return_value=_roles(everyone_can_view=True)),
        patch("apps.events.views.config") as cfg,
    ):
        cfg.GUILD_ID = GUILD_ID
        cfg.EVENT_ROLE_PREFIXES = []
        client.get(url)
        client.get(url)

    assert fetch.call_count == 1, "the second render should have used the cache"


@pytest.mark.django_db
def test_an_outage_is_not_cached(client, event, event_admin):
    """Caching a failure would hold on to the outage after Discord came back."""
    Squad.objects.create(event=event, name="A", discord_channel_id=CHANNEL_ID)
    client.force_login(event_admin)
    url = reverse("events:squad_manage", args=[event.pk])

    with (
        patch("apps.accounts.discord_service.get_guild_channels", return_value=None) as fetch,
        patch("apps.accounts.discord_service.get_guild_roles", return_value=None),
        patch("apps.events.views.config") as cfg,
    ):
        cfg.GUILD_ID = GUILD_ID
        cfg.EVENT_ROLE_PREFIXES = []
        client.get(url)
        client.get(url)

    assert fetch.call_count == 2, "a failed read must be retried, not cached"
