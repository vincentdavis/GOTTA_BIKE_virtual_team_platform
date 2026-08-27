"""Guild member sync survives a user who replaced their Discord account.

`GuildMember.user` is a OneToOneField, so a user holds exactly one row. When somebody
loses a Discord account and makes a new one, `User.discord_id` moves to the new account
while the old GuildMember keeps the link. The next sync then tried to create a second row
for the same user and died on accounts_guildmember_user_id_key -- taking the entire sweep
with it, so no member was updated and no departure was stamped.
"""

import pytest

from apps.accounts.models import GuildMember
from apps.accounts.services import apply_guild_member_sync

OLD_ID = "111111111111111111"
NEW_ID = "222222222222222222"


def _member(discord_id, username="rider"):
    """Build one normalized member payload entry.

    Returns:
        The dict apply_guild_member_sync expects.

    """
    return {
        "discord_id": discord_id, "username": username, "display_name": username,
        "nickname": "", "avatar_hash": "", "roles": [], "joined_at": None, "is_bot": False,
    }


@pytest.fixture
def rider(user_model):
    """Build a user who moved to a new Discord account, old GuildMember still linked.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(
        username="rider", email="rider@example.test", discord_id=NEW_ID,
    )
    GuildMember.objects.create(discord_id=OLD_ID, username="rider-old", user=user)
    return user


@pytest.mark.django_db
def test_the_sync_no_longer_dies_on_the_replaced_account(rider) -> None:
    """Reproduce the production failure: IntegrityError aborting the whole sweep."""
    result = apply_guild_member_sync([_member(NEW_ID), _member("333333333333333333", "other")])

    assert result["failed"] == 0
    assert result["created"] == 2


@pytest.mark.django_db
def test_the_old_row_is_kept_and_only_the_link_released(rider) -> None:
    """It is the record that that Discord account was in the guild, so it stays."""
    apply_guild_member_sync([_member(NEW_ID)])

    old = GuildMember.objects.get(discord_id=OLD_ID)
    assert old.user is None
    assert old.username == "rider-old"


@pytest.mark.django_db
def test_the_new_row_takes_over_the_user(rider) -> None:
    """The live Discord identity is the one that should be linked."""
    result = apply_guild_member_sync([_member(NEW_ID)])

    assert GuildMember.objects.get(discord_id=NEW_ID).user == rider
    assert result["relinked"] == 1


@pytest.mark.django_db
def test_an_existing_row_can_also_claim_the_link(rider, user_model) -> None:
    """The same collision exists on the update path, not only on create."""
    GuildMember.objects.create(discord_id=NEW_ID, username="rider-new", user=None)

    apply_guild_member_sync([_member(NEW_ID)])

    assert GuildMember.objects.get(discord_id=NEW_ID).user == rider
    assert GuildMember.objects.get(discord_id=OLD_ID).user is None


@pytest.mark.django_db
def test_one_unusable_member_no_longer_aborts_the_sweep(user_model) -> None:
    """Before this, a single bad row meant zero members synced and no departures stamped."""
    # A malformed entry: no discord_id at all. Deliberately not an over-long username --
    # SQLite ignores max_length, so that only fails on the Postgres this runs on live.
    bad = {"username": "no-id"}

    result = apply_guild_member_sync([bad, _member("555555555555555555", "good")])

    assert result["failed"] == 1
    assert GuildMember.objects.filter(discord_id="555555555555555555").exists()


@pytest.mark.django_db
def test_a_clean_sync_reports_no_relinks(user_model) -> None:
    """The counter should stay quiet when nothing unusual happened."""
    user_model.objects.create_user(username="a", email="a@example.test", discord_id=NEW_ID)

    result = apply_guild_member_sync([_member(NEW_ID)])

    assert result["relinked"] == 0
    assert result["failed"] == 0
