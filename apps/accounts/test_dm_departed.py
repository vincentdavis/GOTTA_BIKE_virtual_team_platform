"""Nobody who has left the team's Discord server is sent a DM.

A rider who had left the server kept getting the nightly "verification expiring" DM: the
reminder task asked only whether they had opted out. The departure is enforced where the
opt-out is, inside send_discord_dm, so every DM the app sends -- reminders, review results,
captain and performance-team notices, signup confirmations -- stops at once, and a new
caller cannot forget it. The guild sync's ``date_left`` stamp decides it, as it decides access.
"""

from unittest.mock import patch

import pytest
from constance.test import override_config
from django.db.models import F
from django.utils import timezone

from apps.accounts.discord_service import send_discord_dm
from apps.accounts.membership import clear_departure, departed_discord_ids
from apps.accounts.models import GuildMember

DISCORD_ID = "555000111"


def _guild_row(discord_id=DISCORD_ID, *, left=False):
    """Make the GuildMember row the guild sync would have written.

    Returns:
        The row.

    """
    return GuildMember.objects.create(
        discord_id=discord_id,
        username="someone",
        date_left=timezone.now() if left else None,
    )


def _sends(discord_id=DISCORD_ID):
    """Send a DM with the HTTP client patched out.

    Returns:
        ``(whether a request was made, what send_discord_dm returned)``.

    """
    with patch("apps.accounts.discord_service.httpx.Client") as client:
        result = send_discord_dm(discord_id, "hello")
    return client.called, result


@pytest.mark.django_db
def test_no_request_is_made_for_a_member_who_has_left():
    _guild_row(left=True)

    requested, result = _sends()

    assert not requested
    assert result is True, (
        "a deliberate skip must read as success: callers use the return value to decide "
        "whether to retry and whether to stamp 'already warned'"
    )


@pytest.mark.django_db
@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_a_member_still_in_the_server_gets_the_request():
    _guild_row(left=False)

    requested, _ = _sends()

    assert requested


@pytest.mark.django_db
def test_an_account_never_seen_in_the_server_gets_nothing():
    """The sync's marker for an account it never saw: date_left copied from date_created."""
    row = _guild_row(left=True)
    GuildMember.objects.filter(pk=row.pk).update(date_left=F("date_created"))

    requested, _ = _sends()

    assert not requested


@pytest.mark.django_db
def test_staff_are_not_exempt_from_the_dm_gate(user_model):
    """Staff keep their admin login when they leave; a DM to their Discord account still stops."""
    user_model.objects.create_user(
        username="staffer", email="staffer@example.test", discord_id=DISCORD_ID, is_staff=True
    )
    _guild_row(left=True)

    requested, _ = _sends()

    assert not requested


@pytest.mark.django_db
@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_an_id_with_no_member_row_is_unaffected():
    """No row means no verdict yet (a rider who joined since the last sync), as for access."""
    requested, _ = _sends()

    assert requested


@pytest.mark.django_db
@override_config(DISCORD_BOT_TOKEN="test-token")  # noqa: S106
def test_a_member_who_rejoins_gets_dms_again():
    _guild_row(left=True)
    clear_departure(DISCORD_ID)

    requested, _ = _sends()

    assert requested


@pytest.mark.django_db
def test_departed_ids_are_found_in_one_query(django_assert_num_queries):
    _guild_row("1", left=True)
    _guild_row("2", left=False)

    with django_assert_num_queries(1):
        departed = departed_discord_ids(["1", "2", "3", None, ""])

    assert departed == {"1"}
