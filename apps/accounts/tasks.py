"""Background tasks for accounts app."""

import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]

from apps.accounts.discord_service import send_discord_channel_message
from apps.accounts.models import User


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
