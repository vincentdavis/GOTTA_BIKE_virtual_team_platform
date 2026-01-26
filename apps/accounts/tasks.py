"""Background tasks for accounts app."""

import time

import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.accounts.discord_service import (
    add_discord_role,
    remove_discord_role,
    send_discord_channel_message,
)
from apps.accounts.models import GuildMember, User


@task
def notify_rider_left_team(zwid: int, rider_name: str, source: str) -> dict:
    """Send Discord notification when a rider leaves the team.

    Args:
        zwid: The Zwift ID of the rider who left.
        rider_name: The display name of the rider.
        source: The data source ("ZwiftPower" or "Zwift Racing").

    Returns:
        dict with notification status.

    """
    with logfire.span("notify_rider_left_team", zwid=zwid, source=source):
        channel_id = config.USER_CHANGE_LOG

        if not channel_id or channel_id == 0:
            logfire.debug("USER_CHANGE_LOG channel not configured")
            return {"status": "skipped", "reason": "channel_not_configured"}

        # Try to find user's Discord info for mention
        discord_mention = ""
        try:
            user = User.objects.get(zwid=zwid)
            if user.discord_id:
                discord_mention = f" (<@{user.discord_id}>)"
        except User.DoesNotExist:
            pass

        message = (
            f"**Rider Left Team**\n"
            f"{rider_name}{discord_mention} has left the {source} team roster.\n"
            f"Zwift ID: `{zwid}`"
        )

        success = send_discord_channel_message(channel_id, message)

        return {
            "status": "sent" if success else "failed",
            "zwid": zwid,
            "source": source,
        }


@task
def guild_member_sync_status() -> dict:
    """Report on guild member sync health and statistics.

    This task checks the GuildMember table for sync health metrics.
    The actual sync is performed by the Discord bot on a schedule.

    Returns:
        dict with sync status and statistics.

    """
    with logfire.span("guild_member_sync_status"):
        now = timezone.now()

        # Get counts
        total_members = GuildMember.objects.count()
        active_members = GuildMember.objects.filter(date_left__isnull=True).count()
        left_members = GuildMember.objects.filter(date_left__isnull=False).count()
        linked_members = GuildMember.objects.filter(user__isnull=False, date_left__isnull=True).count()
        bot_members = GuildMember.objects.filter(is_bot=True, date_left__isnull=True).count()

        # Get last sync time (most recently modified record)
        last_modified = GuildMember.objects.order_by("-date_modified").values_list("date_modified", flat=True).first()

        # Calculate time since last sync
        hours_since_sync = None
        if last_modified:
            delta = now - last_modified
            hours_since_sync = round(delta.total_seconds() / 3600, 1)

        status = {
            "total_records": total_members,
            "active_members": active_members,
            "left_members": left_members,
            "linked_to_users": linked_members,
            "bot_accounts": bot_members,
            "last_sync": last_modified.isoformat() if last_modified else None,
            "hours_since_sync": hours_since_sync,
            "sync_source": "discord_bot",
            "note": "Guild member sync is performed by the Discord bot every 6 hours",
        }

        logfire.info("Guild member sync status", **status)

        return status


@task
def sync_race_ready_roles() -> dict:
    """Sync race ready Discord roles for all users.

    Checks each user's is_race_ready status and ensures their Discord
    role matches. Adds the role if they should have it, removes if not.

    Returns:
        dict with sync results summary.

    """
    with logfire.span("sync_race_ready_roles"):
        # Get race ready role ID from constance
        race_ready_role_id = config.RACE_READY_ROLE_ID
        if not race_ready_role_id or race_ready_role_id == 0:
            logfire.info("RACE_READY_ROLE_ID not configured, skipping sync")
            return {"status": "skipped", "reason": "role_not_configured"}

        role_id_str = str(race_ready_role_id)

        # Get all users with Discord IDs
        users_with_discord = User.objects.exclude(discord_id="").exclude(discord_id__isnull=True)
        total_users = users_with_discord.count()

        logfire.info("Starting race ready role sync", total_users=total_users)

        # Track results
        added = 0
        removed = 0
        unchanged = 0
        errors = 0
        users_added = []
        users_removed = []

        for user in users_with_discord.iterator():
            # Check if user is race ready
            is_race_ready = user.is_race_ready

            # Check if user currently has the role
            has_role = role_id_str in (user.discord_roles or {})

            # Determine action needed
            if is_race_ready and not has_role:
                # User should have the role but doesn't - add it
                success = add_discord_role(user.discord_id, role_id_str)
                if success:
                    added += 1
                    users_added.append(user)
                    # Update local discord_roles to reflect the change
                    if user.discord_roles is None:
                        user.discord_roles = {}
                    user.discord_roles[role_id_str] = "Race Ready"
                    user.save(update_fields=["discord_roles"])
                else:
                    errors += 1
                # Rate limit: 0.5s delay between API calls
                time.sleep(0.5)

            elif not is_race_ready and has_role:
                # User has the role but shouldn't - remove it
                success = remove_discord_role(user.discord_id, role_id_str)
                if success:
                    removed += 1
                    users_removed.append(user)
                    # Update local discord_roles to reflect the change
                    if user.discord_roles and role_id_str in user.discord_roles:
                        del user.discord_roles[role_id_str]
                        user.save(update_fields=["discord_roles"])
                else:
                    errors += 1
                # Rate limit: 0.5s delay between API calls
                time.sleep(0.5)

            else:
                # No change needed
                unchanged += 1

        # Send notifications for role changes
        channel_id = config.USER_CHANGE_LOG
        if channel_id and channel_id != 0:
            # Notify about users who gained the role
            for user in users_added:
                name = _get_user_display_name(user)
                mention = f"<@{user.discord_id}>"
                message = (
                    f"🏁 **Race Ready Role Added** (scheduled sync)\n"
                    f"{name} ({mention}) gained the race ready role."
                )
                send_discord_channel_message(channel_id, message, silent=True)
                time.sleep(0.2)  # Rate limit notifications

            # Notify about users who lost the role
            for user in users_removed:
                name = _get_user_display_name(user)
                mention = f"<@{user.discord_id}>"
                message = (
                    f"⚠️ **Race Ready Role Removed** (scheduled sync)\n"
                    f"{name} ({mention}) lost the race ready role due to expired/missing verifications."
                )
                send_discord_channel_message(channel_id, message, silent=True)
                time.sleep(0.2)  # Rate limit notifications

        result = {
            "status": "completed",
            "total_users": total_users,
            "added": added,
            "removed": removed,
            "unchanged": unchanged,
            "errors": errors,
        }

        logfire.info("Race ready role sync completed", **result)

        return result


def _get_user_display_name(user: User) -> str:
    """Get display name for a user.

    Args:
        user: User instance.

    Returns:
        Best available display name for the user.

    """
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    if user.first_name:
        return user.first_name
    return user.discord_nickname or user.discord_username or f"User {user.id}"
