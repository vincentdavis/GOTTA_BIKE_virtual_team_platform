"""The "Who can see this channel?" action on the Manage Squads panel."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.models import GuildMember
from apps.events.channel_access import VIEW_CHANNEL
from apps.events.models import Event, Squad, SquadMember

GUILD_ID = "1"
SQUAD_ROLE = "100"
CHANNEL = 555


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=7), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a Discord channel and role.

    Returns:
        The squad.

    """
    return Squad.objects.create(
        event=event, name="Div 1", discord_channel_id=CHANNEL, team_discord_role=int(SQUAD_ROLE)
    )


def _person(user_model, username, discord_id, *, roles, squad=None):
    """Create a user with a matching GuildMember carrying Discord roles.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test", discord_id=discord_id,
    )
    GuildMember.objects.create(
        discord_id=discord_id, username=username, display_name=username.title(),
        roles=roles, user=user,
    )
    if squad is not None:
        SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _discord(overwrites):
    """Patch the two Discord reads with a channel and role set.

    Returns:
        A context manager patching both client calls.

    """
    channel = {"name": "div-1", "permission_overwrites": overwrites}
    roles = [{"id": GUILD_ID, "name": "@everyone", "permissions": str(VIEW_CHANNEL)},
             {"id": SQUAD_ROLE, "name": "Div 1", "permissions": "0"}]
    return patch.multiple(
        "apps.accounts.discord_service",
        get_channel=lambda _cid: channel,
        get_guild_roles=lambda: roles,
    )


def _load(client, event, squad):
    """Request the channel-access panel.

    Returns:
        The decoded body.

    """
    response = client.get(reverse("events:squad_channel_access", args=[event.pk, squad.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_someone_outside_the_squad_who_can_see_the_channel_is_named(
    client, event, squad, superuser, user_model
) -> None:
    """The reason this exists: channel access nobody granted on purpose."""
    _person(user_model, "insider", "d1", roles=[SQUAD_ROLE], squad=squad)
    _person(user_model, "outsider", "d2", roles=[SQUAD_ROLE])
    client.force_login(superuser)

    overwrites = [
        {"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)},
        {"id": SQUAD_ROLE, "type": 0, "allow": str(VIEW_CHANNEL), "deny": "0"},
    ]
    with patch("apps.events.views.config") as cfg, _discord(overwrites):
        cfg.GUILD_ID = GUILD_ID
        body = _load(client, event, squad)

    assert "not on this squad" in body
    assert "Outsider" in body
    assert "Insider" not in body


@pytest.mark.django_db
def test_a_squad_member_locked_out_of_their_own_channel_is_flagged(
    client, event, squad, superuser, user_model
) -> None:
    """The mirror case -- they are on the squad but cannot read it."""
    _person(user_model, "roleless", "d3", roles=[], squad=squad)
    client.force_login(superuser)

    overwrites = [
        {"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)},
        {"id": SQUAD_ROLE, "type": 0, "allow": str(VIEW_CHANNEL), "deny": "0"},
    ]
    with patch("apps.events.views.config") as cfg, _discord(overwrites):
        cfg.GUILD_ID = GUILD_ID
        body = _load(client, event, squad)

    assert "cannot see the channel" in body
    assert "Roleless" in body


@pytest.mark.django_db
def test_a_clean_channel_says_so(client, event, squad, superuser, user_model) -> None:
    """A wall of empty sections would not read as "nothing wrong here"."""
    _person(user_model, "insider", "d1", roles=[SQUAD_ROLE], squad=squad)
    client.force_login(superuser)

    overwrites = [
        {"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)},
        {"id": SQUAD_ROLE, "type": 0, "allow": str(VIEW_CHANNEL), "deny": "0"},
    ]
    with patch("apps.events.views.config") as cfg, _discord(overwrites):
        cfg.GUILD_ID = GUILD_ID
        body = _load(client, event, squad)

    assert "Everyone who can see this channel is on the squad" in body


@pytest.mark.django_db
def test_a_squad_with_no_channel_says_so_rather_than_calling_discord(
    client, event, superuser
) -> None:
    """No channel configured is a normal state, not an error to retry."""
    bare = Squad.objects.create(event=event, name="No channel")
    client.force_login(superuser)

    with patch("apps.accounts.discord_service.get_channel") as fetch:
        body = _load(client, event, bare)

    fetch.assert_not_called()
    assert "no Discord channel configured" in body


@pytest.mark.django_db
def test_a_discord_failure_degrades_instead_of_erroring(client, event, squad, superuser) -> None:
    """A bot permission problem must not 500 the Manage Squads page."""
    client.force_login(superuser)

    with patch("apps.accounts.discord_service.get_channel", return_value=None), patch(
        "apps.accounts.discord_service.get_guild_roles", return_value=None
    ):
        body = _load(client, event, squad)

    assert "Could not read the channel from Discord" in body


@pytest.mark.django_db
def test_a_plain_team_member_cannot_run_the_audit(client, event, squad, team_member) -> None:
    """It lists guild members by name, so it stays behind squad management."""
    client.force_login(team_member)

    response = client.get(reverse("events:squad_channel_access", args=[event.pk, squad.pk]))

    assert response.status_code == 403
