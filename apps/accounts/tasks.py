"""Background tasks for accounts app."""

import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.accounts.discord_service import send_discord_channel_message
from apps.accounts.models import GuildMember, User
from apps.team.models import DiscordRole


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
