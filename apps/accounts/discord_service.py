"""Discord API service for sending direct messages."""

import httpx
import logfire
from constance import config

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


def send_verification_notification(
    discord_id: str,
    is_verified: bool,
    verify_type: str,
    rejection_reason: str | None = None,
) -> bool:
    """Send a verification status notification to a user.

    Args:
        discord_id: The Discord user ID to notify.
        is_verified: True if verified, False if rejected.
        verify_type: The type of verification (e.g., "weight_full", "height").
        rejection_reason: Optional reason for rejection.

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
    else:
        message = (
            f"❌ **Verification Rejected**\n\n"
            f"Your **{type_display}** verification record has been rejected."
        )
        if rejection_reason:
            message += f"\n\n**Reason:** {rejection_reason}"
        message += "\n\nPlease submit a new verification record with the required corrections."

    return send_discord_dm(discord_id, message)
