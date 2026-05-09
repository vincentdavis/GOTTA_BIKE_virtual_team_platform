"""Discord API service for sending direct messages and syncing roles."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import logfire
from constance import config

if TYPE_CHECKING:
    from apps.accounts.models import User

DISCORD_API_BASE = "https://discord.com/api/v10"


def send_discord_dm(discord_id: str, message: str) -> bool:
    """Send a direct message to a Discord user.

    Args:
        discord_id: The Discord user ID to send the message to.
        message: The message content to send.

    Returns:
        True if the message was sent successfully, False otherwise.

    """
    bot_token = config.DISCORD_BOT_TOKEN
    if not bot_token:
        logfire.warning("DISCORD_BOT_TOKEN not configured, skipping DM")
        return False

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            # Step 1: Create a DM channel with the user
            create_dm_response = client.post(
                f"{DISCORD_API_BASE}/users/@me/channels",
                headers=headers,
                json={"recipient_id": discord_id},
            )
            create_dm_response.raise_for_status()
            channel_id = create_dm_response.json()["id"]

            # Step 2: Send the message to the DM channel
            send_response = client.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=headers,
                json={"content": message},
            )
            send_response.raise_for_status()

            logfire.info("Discord DM sent successfully", discord_id=discord_id)
            return True

    except httpx.HTTPStatusError as e:
        logfire.error(
            "Failed to send Discord DM",
            discord_id=discord_id,
            status_code=e.response.status_code,
            error=str(e),
        )
        return False
    except httpx.RequestError as e:
        logfire.error(
            "Discord API request failed",
            discord_id=discord_id,
            error=str(e),
        )
        return False


def send_discord_channel_message(
    channel_id: str | int,
    message: str,
    *,
    silent: bool = False,
    allowed_user_ids: list[str] | None = None,
    allowed_role_ids: list[str] | None = None,
) -> bool:
    """Send a message to a Discord channel.

    Args:
        channel_id: The Discord channel ID to send the message to.
        message: The message content to send.
        silent: If True, suppress push/desktop notifications for this message.
        allowed_user_ids: Optional list of Discord user IDs whose @mentions should
            actually trigger notifications. When provided, sets ``allowed_mentions``
            to restrict pings to exactly those users (everyone/role mentions are
            disabled unless ``allowed_role_ids`` is also provided).
        allowed_role_ids: Optional list of Discord role IDs whose @mentions should
            actually trigger notifications. When provided, ``allowed_mentions``
            includes the listed roles so ``<@&role_id>`` syntax pings members of
            those roles.

    Returns:
        True if the message was sent successfully, False otherwise.

    """
    if not channel_id or channel_id == 0:
        logfire.debug("Channel ID is 0 or not set, skipping channel message")
        return False

    bot_token = config.DISCORD_BOT_TOKEN
    if not bot_token:
        logfire.warning("DISCORD_BOT_TOKEN not configured, skipping channel message")
        return False

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    # Build message payload
    payload: dict = {"content": message}
    if silent:
        # Flag 4096 (1 << 12) = SUPPRESS_NOTIFICATIONS
        payload["flags"] = 4096
    if allowed_user_ids is not None or allowed_role_ids is not None:
        payload["allowed_mentions"] = {
            "parse": [],
            "users": allowed_user_ids or [],
            "roles": allowed_role_ids or [],
        }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            logfire.info("Discord channel message sent", channel_id=str(channel_id), silent=silent)
            return True

    except httpx.HTTPStatusError as e:
        logfire.error(
            "Failed to send Discord channel message",
            channel_id=str(channel_id),
            status_code=e.response.status_code,
            error=str(e),
        )
        return False
    except httpx.RequestError as e:
        logfire.error(
            "Discord API request failed for channel message",
            channel_id=str(channel_id),
            error=str(e),
        )
        return False


def create_discord_thread(
    channel_id: str | int,
    name: str,
    auto_archive_minutes: int = 1440,
) -> tuple[int | None, str | None]:
    """Create a public thread in a Discord text channel.

    POSTs to ``/channels/{channel_id}/threads`` with ``type=11`` (GUILD_PUBLIC_THREAD).
    Discord caps thread names at 100 characters; the name is truncated if longer.

    Args:
        channel_id: The parent Discord channel ID.
        name: The thread name. Truncated to 100 characters.
        auto_archive_minutes: Auto-archive duration in minutes. Allowed: 60, 1440, 4320, 10080.

    Returns:
        Tuple of (thread_id, error_message). On success, error_message is None.
        On failure, thread_id is None and error_message describes the problem.

    """
    if not channel_id or channel_id == 0:
        return None, "No Discord channel configured."

    bot_token = config.DISCORD_BOT_TOKEN
    if not bot_token:
        return None, "Discord bot token is not configured."

    truncated_name = name[:100]
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "name": truncated_name,
        "type": 11,
        "auto_archive_duration": auto_archive_minutes,
    }

    with logfire.span("create_discord_thread", channel_id=str(channel_id), name=truncated_name):
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{DISCORD_API_BASE}/channels/{channel_id}/threads",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                thread_id = int(response.json()["id"])
                logfire.info(
                    "Discord thread created",
                    channel_id=str(channel_id),
                    thread_id=str(thread_id),
                )
                return thread_id, None
        except httpx.HTTPStatusError as e:
            try:
                err_body = e.response.json()
                err_msg = err_body.get("message") or str(e)
            except ValueError:
                err_msg = e.response.text or str(e)
            logfire.error(
                "Failed to create Discord thread",
                channel_id=str(channel_id),
                status_code=e.response.status_code,
                error=err_msg,
            )
            return None, err_msg
        except httpx.RequestError as e:
            logfire.error(
                "Discord API request failed for thread creation",
                channel_id=str(channel_id),
                error=str(e),
            )
            return None, f"Discord API request failed: {e}"


def send_verification_notification(
    discord_id: str,
    is_verified: bool,
    verify_type: str,
    review_note: str | None = None,
) -> bool:
    """Send a verification status notification to a user.

    Args:
        discord_id: The Discord user ID to notify.
        is_verified: True if verified, False if rejected.
        verify_type: The type of verification (e.g., "weight_full", "height").
        review_note: Optional reviewer note.

    Returns:
        True if the message was sent successfully, False otherwise.

    """
    # Format verify type for display
    type_display = verify_type.replace("_", " ").title()

    if is_verified:
        message = (
            f"✅ **Verification Approved**\n\n"
            f"Your **{type_display}** verification record has been approved.\n\n"
            f"Thank you for completing the verification process!"
        )
        if review_note:
            message += f"\n\n**Note:** {review_note}"
    else:
        message = (
            f"❌ **Verification Rejected**\n\n"
            f"Your **{type_display}** verification record has been rejected."
        )
        if review_note:
            message += f"\n\n**Reason:** {review_note}"
        message += "\n\nPlease submit a new verification record with the required corrections."

    return send_discord_dm(discord_id, message)


def add_discord_role(discord_id: str, role_id: str) -> bool:
    """Add a role to a Discord guild member.

    Args:
        discord_id: The Discord user ID.
        role_id: The Discord role ID to add.

    Returns:
        True if the role was added successfully, False otherwise.

    """
    bot_token = config.DISCORD_BOT_TOKEN
    if not bot_token:
        logfire.warning("DISCORD_BOT_TOKEN not configured, skipping role add")
        return False

    guild_id = config.GUILD_ID
    if not guild_id:
        logfire.warning("GUILD_ID not configured, skipping role add")
        return False

    headers = {
        "Authorization": f"Bot {bot_token}",
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.put(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{discord_id}/roles/{role_id}",
                headers=headers,
            )
            response.raise_for_status()

            logfire.info(
                "Discord role added",
                discord_id=discord_id,
                role_id=role_id,
                guild_id=guild_id,
            )
            return True

    except httpx.HTTPStatusError as e:
        logfire.error(
            "Failed to add Discord role",
            discord_id=discord_id,
            role_id=role_id,
            status_code=e.response.status_code,
            error=str(e),
        )
        return False
    except httpx.RequestError as e:
        logfire.error(
            "Discord API request failed for role add",
            discord_id=discord_id,
            role_id=role_id,
            error=str(e),
        )
        return False


def remove_discord_role(discord_id: str, role_id: str) -> bool:
    """Remove a role from a Discord guild member.

    Args:
        discord_id: The Discord user ID.
        role_id: The Discord role ID to remove.

    Returns:
        True if the role was removed successfully, False otherwise.

    """
    bot_token = config.DISCORD_BOT_TOKEN
    if not bot_token:
        logfire.warning("DISCORD_BOT_TOKEN not configured, skipping role remove")
        return False

    guild_id = config.GUILD_ID
    if not guild_id:
        logfire.warning("GUILD_ID not configured, skipping role remove")
        return False

    headers = {
        "Authorization": f"Bot {bot_token}",
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.delete(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{discord_id}/roles/{role_id}",
                headers=headers,
            )
            response.raise_for_status()

            logfire.info(
                "Discord role removed",
                discord_id=discord_id,
                role_id=role_id,
                guild_id=guild_id,
            )
            return True

    except httpx.HTTPStatusError as e:
        logfire.error(
            "Failed to remove Discord role",
            discord_id=discord_id,
            role_id=role_id,
            status_code=e.response.status_code,
            error=str(e),
        )
        return False
    except httpx.RequestError as e:
        logfire.error(
            "Discord API request failed for role remove",
            discord_id=discord_id,
            role_id=role_id,
            error=str(e),
        )
        return False


def sync_user_discord_roles(user: User) -> bool:
    """Fetch and sync Discord guild roles for a user.

    Uses the bot token to fetch the user's guild member data from Discord API
    and updates the user's discord_roles field.

    Args:
        user: The User object with discord_id set.

    Returns:
        True if roles were synced successfully, False otherwise.

    """
    if not user.discord_id:
        logfire.warning("Cannot sync roles: user has no discord_id", user_id=user.id)
        return False

    bot_token = config.DISCORD_BOT_TOKEN
    if not bot_token:
        logfire.warning("DISCORD_BOT_TOKEN not configured, skipping role sync")
        return False

    guild_id = config.GUILD_ID
    if not guild_id:
        logfire.warning("GUILD_ID not configured, skipping role sync")
        return False

    headers = {
        "Authorization": f"Bot {bot_token}",
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            # Fetch guild member data
            response = client.get(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{user.discord_id}",
                headers=headers,
            )
            response.raise_for_status()
            member_data = response.json()

        # Extract role IDs from member data
        role_ids = member_data.get("roles", [])

        # Look up role names from our DiscordRole model
        from apps.team.models import DiscordRole

        role_map: dict[str, str] = {}
        for role_id in role_ids:
            try:
                role = DiscordRole.objects.get(role_id=role_id)
                role_map[role_id] = role.name
            except DiscordRole.DoesNotExist:
                # Role not synced yet, use placeholder
                role_map[role_id] = f"Unknown Role ({role_id})"

        # Update user's discord_roles
        user.discord_roles = role_map
        user.save(update_fields=["discord_roles"])

        logfire.info(
            "Discord roles synced for user",
            user_id=user.id,
            discord_id=user.discord_id,
            roles_count=len(role_map),
        )
        return True

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logfire.warning(
                "User not found in guild",
                user_id=user.id,
                discord_id=user.discord_id,
                guild_id=guild_id,
            )
        else:
            logfire.error(
                "Failed to fetch guild member data",
                user_id=user.id,
                discord_id=user.discord_id,
                status_code=e.response.status_code,
                error=str(e),
            )
        return False
    except httpx.RequestError as e:
        logfire.error(
            "Discord API request failed",
            user_id=user.id,
            discord_id=user.discord_id,
            error=str(e),
        )
        return False
