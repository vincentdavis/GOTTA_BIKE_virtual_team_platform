"""Who counts as having left the team's Discord server -- the one rule, in one place.

Only members of the Discord guild may use the site. A Discord login checks that live
(``DiscordSocialAccountAdapter._check_guild_membership``), but a session outlives the login,
so a rider who leaves the server would otherwise keep their access until the session expired.
Two things notice a departure and stamp ``GuildMember.date_left``: the Discord bot, which reports
a member leaving as it happens (``apps.accounts.services.record_member_departure``), and the
scheduled guild-member sync (``apps.accounts.services.apply_guild_member_sync``), the backstop.
The syncs clear the stamp again if they list the member in a list read after it. This
module turns that stamp into an access decision, read by
``apps.accounts.middleware.DepartedMemberLogoutMiddleware`` for every signed-in request and by
``apps.user_api.services.user_can_use_api`` for every API key -- and into a messaging one:
``apps.accounts.discord_service.send_discord_dm`` sends nothing to a departed Discord id.

Deliberately narrow:

- **No ``GuildMember`` row means no verdict yet.** A rider who signed up since the last sync has
  no row, and a local account (no ``discord_id``) never will. Both are left alone. The gap is
  temporary for Discord-linked accounts: the scheduled guild sync (the paginated REST fetch --
  the bot's push is not trusted with departures) writes a departed row for every one its list
  has never included, within its own sanity limit
  (``apps.accounts.services._record_unseen_accounts``), so someone who signs in and leaves
  before a sync sees them is caught by the next one.
- **Staff and superusers are exempt** from the access decision. ``/admin/`` keeps its
  username-and-password login, by the owner's decision, and the account that runs it must not be
  locked out by a Discord sync. They are not exempt from the DM gate: a DM goes to the Discord
  account, and a departed one is no longer in the server whoever owns it.
- **The row is found by the user's current ``discord_id``**, not through the
  ``GuildMember.user`` link: when somebody moves to a new Discord account the link can still
  point at the old, departed one (see ``apps.accounts.services._release_user_link``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Iterable

    from apps.accounts.models import User


def is_exempt_from_guild_rule(user: User) -> bool:
    """Whether this account is outside the guild-membership rule altogether.

    Args:
        user: The account to check.

    Returns:
        True for staff and superusers.

    """
    return bool(user.is_staff or user.is_superuser)


def has_left_guild(user: User) -> bool:
    """Whether the guild sync has marked this account's Discord id as departed.

    One indexed lookup (``GuildMember.discord_id`` is unique). An account with no
    ``discord_id`` is answered without touching the database.

    Args:
        user: The account to check.

    Returns:
        True if a ``GuildMember`` row for ``user.discord_id`` has ``date_left`` set.

    """
    return discord_id_has_left_guild(getattr(user, "discord_id", "") or "")


def discord_id_has_left_guild(discord_id: str | int | None) -> bool:
    """Whether the guild sync has marked this Discord id as departed.

    The same rule as :func:`has_left_guild`, for callers holding an id rather than an account
    (a DM is addressed to a Discord id). One indexed lookup; a blank id is answered without
    touching the database, and an id with no ``GuildMember`` row has no verdict, so False.

    Args:
        discord_id: The Discord id to check.

    Returns:
        True if the ``GuildMember`` row for it has ``date_left`` set.

    """
    from apps.accounts.models import GuildMember

    if not discord_id:
        return False
    return GuildMember.objects.filter(discord_id=str(discord_id), date_left__isnull=False).exists()


def departed_discord_ids(discord_ids: Iterable[str | int | None]) -> set[str]:
    """Return which of these Discord ids the guild sync has marked as departed, in one query.

    Args:
        discord_ids: Discord ids; blanks are skipped.

    Returns:
        The departed ones, as strings.

    """
    from apps.accounts.models import GuildMember

    wanted = {str(discord_id) for discord_id in discord_ids if discord_id}
    if not wanted:
        return set()
    return set(
        GuildMember.objects.filter(discord_id__in=wanted, date_left__isnull=False).values_list("discord_id", flat=True)
    )


def is_departed_member(user: User) -> bool:
    """Whether this account has lost access because its owner left the Discord server.

    The access decision: a non-staff, non-superuser account whose Discord id the guild
    sync has marked as departed.

    Args:
        user: The account to check.

    Returns:
        True if the account must be refused.

    """
    if not getattr(user, "is_authenticated", False):
        return False
    if is_exempt_from_guild_rule(user):
        return False
    return has_left_guild(user)


def clear_departure(discord_id: str | int | None) -> int:
    """Clear a stale departure stamp once Discord has confirmed the member is back.

    Called after a Discord login passes the live guild check. Without it a rider who
    rejoined the server would sign in, then be signed out again on the very next request,
    until the next guild sync (up to its interval) caught up. The sync clears the stamp
    too when it lists them, if its list was read after the stamp.

    Args:
        discord_id: The Discord id Discord has just confirmed is in the guild.

    Returns:
        The number of rows cleared (0 or 1).

    """
    from apps.accounts.models import GuildMember

    if not discord_id:
        return 0
    rows = GuildMember.objects.filter(discord_id=str(discord_id), date_left__isnull=False)
    # A queryset update skips auto_now, so stamp date_modified by hand as save() would.
    return rows.update(date_left=None, date_modified=timezone.now())
