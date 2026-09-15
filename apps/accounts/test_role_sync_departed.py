"""Role syncs must not chase people who have left the Discord server.

Both role syncs iterated every user who ever had a ``discord_id``. Discord answers 404
Unknown Member for anyone no longer in the guild, so this showed up in production as 68
"Failed to add Discord role" errors in a fortnight, all for the same ZR role and the same
handful of riders, on every scheduled run.

The wasted calls are the visible half. The quiet half is worse: the ZR sync carries on past
the failure and posts an upgrade announcement, naming and @-mentioning a rider who is not
there to read it.

The other edge matters as much and pulls the opposite way -- a rider with no ``GuildMember``
row at all must still be synced. The row comes from the guild sync, so its absence means that
sync has not reached them, not that they are gone. Excluding them would silently stop role
management for every new rider.
"""

import pytest
from constance.test import override_config
from django.utils import timezone

from apps.accounts.models import GuildMember
from apps.accounts.tasks import _users_in_the_guild

ZR_GOLD = "1469679273014071399"


def _linked(user_model, username, *, discord_id, left=False, seen=True):
    """Make a Discord-linked user, optionally with a guild row marking them present or gone.

    Returns:
        The user.

    """
    user = user_model.objects.create_user(username=username, password="pw")  # noqa: S106
    user.discord_id = discord_id
    user.save(update_fields=["discord_id"])
    if seen:
        GuildMember.objects.create(
            discord_id=discord_id,
            username=username,
            user=user,
            date_left=timezone.now() if left else None,
        )
    return user


# --- who the syncs will touch ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_departed_member_is_left_alone(user_model):
    gone = _linked(user_model, "gone", discord_id="900001", left=True)

    assert gone not in _users_in_the_guild()


@pytest.mark.django_db
def test_a_current_member_is_synced(user_model):
    here = _linked(user_model, "here", discord_id="900002")

    assert here in _users_in_the_guild()


@pytest.mark.django_db
def test_a_rider_the_guild_sync_has_not_seen_is_still_synced(user_model):
    """No guild row means "not yet seen", not "left" -- the opposite edge, and easy to break."""
    unseen = _linked(user_model, "unseen", discord_id="900003", seen=False)

    assert unseen in _users_in_the_guild()


@pytest.mark.django_db
def test_a_rider_with_no_discord_link_is_not_synced(user_model):
    """Unchanged behaviour, pinned so the rewrite did not quietly widen the set."""
    user_model.objects.create_user(username="nodiscord", password="pw")  # noqa: S106

    assert not _users_in_the_guild().filter(username="nodiscord").exists()


@pytest.mark.django_db
def test_a_rider_who_left_and_came_back_is_synced_again(user_model):
    """date_left is cleared when the guild sync sees them again, and that must be enough."""
    returner = _linked(user_model, "returner", discord_id="900004", left=True)
    GuildMember.objects.filter(discord_id="900004").update(date_left=None)

    assert returner in _users_in_the_guild()


# --- through the task itself -----------------------------------------------------------------


@pytest.mark.django_db
@override_config(ZR_ROLE_GOLD=ZR_GOLD, ZR_UPGRADE_NOTICE_CHANNEL=0)
def test_the_zr_sync_makes_no_discord_call_for_a_departed_rider(user_model, monkeypatch):
    """End to end: the 404s in production were this task, on these riders."""
    from apps.accounts import tasks
    from apps.zwiftracing.models import ZRRider

    gone = _linked(user_model, "gone", discord_id="900001", left=True)
    gone.zwid = 4242
    gone.save(update_fields=["zwid"])
    ZRRider.objects.create(zwid=4242, name="Gone Rider", race_current_category="Gold")

    calls = []
    monkeypatch.setattr(tasks, "add_discord_role", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(tasks, "remove_discord_role", lambda *a, **k: calls.append(a) or True)

    result = tasks.sync_zr_category_roles.func()

    assert calls == [], f"tried to set roles for a rider who left: {calls}"
    assert result.get("errors", 0) == 0


@pytest.mark.django_db
@override_config(ZR_ROLE_GOLD=ZR_GOLD, ZR_UPGRADE_NOTICE_CHANNEL=0)
def test_the_zr_sync_still_assigns_roles_to_riders_who_are_here(user_model, monkeypatch):
    """The guard must not take the feature with it."""
    from apps.accounts import tasks
    from apps.zwiftracing.models import ZRRider

    here = _linked(user_model, "here", discord_id="900002")
    here.zwid = 4243
    here.save(update_fields=["zwid"])
    ZRRider.objects.create(zwid=4243, name="Here Rider", race_current_category="Gold")

    calls = []
    monkeypatch.setattr(tasks, "add_discord_role", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(tasks, "remove_discord_role", lambda *a, **k: calls.append(a) or True)

    tasks.sync_zr_category_roles.func()

    assert calls == [("900002", ZR_GOLD)]


@pytest.mark.django_db
@override_config(RACE_READY_ROLE_ID=5551234)
def test_the_race_ready_sync_skips_departed_riders_too(user_model, monkeypatch):
    """Same flaw, same file, not yet firing in production -- fixed before it does."""
    from apps.accounts import tasks

    gone = _linked(user_model, "gone", discord_id="900001", left=True)
    gone.is_race_ready = True
    gone.save(update_fields=["is_race_ready"])

    calls = []
    monkeypatch.setattr(tasks, "add_discord_role", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(tasks, "remove_discord_role", lambda *a, **k: calls.append(a) or True)

    tasks.sync_race_ready_roles.func()

    assert calls == []
