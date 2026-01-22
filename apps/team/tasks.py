"""Background tasks for team app."""

import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]

from apps.accounts.discord_service import send_discord_channel_message
from apps.team.models import MembershipApplication


@task
def notify_application_update(
    application_id: str,
    update_type: str,
    admin_name: str | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    application_url: str | None = None,
) -> dict:
    """Send Discord notification for membership application updates.

    Sends a message to WELCOME_TEAM_CHANNEL_ID when an application is created,
    updated by applicant, or modified by an admin.

    Args:
        application_id: UUID of the MembershipApplication.
        update_type: Type of update - "created", "applicant_updated",
            "status_changed", or "admin_notes".
        admin_name: Name of the admin who made the change (for admin actions).
        old_status: Previous status (for status changes).
        new_status: New status (for status changes).
        application_url: Full URL to the application admin page.

    Returns:
        dict with notification status.

    """
    with logfire.span(
        "notify_application_update",
        application_id=application_id,
        update_type=update_type,
    ):
        channel_id = config.REGISTRATION_UPDATES_CHANNEL_ID

        if not channel_id or channel_id == 0:
            logfire.debug("REGISTRATION_UPDATES_CHANNEL_ID not configured, skipping notification")
            return {"status": "skipped", "reason": "channel_not_configured"}

        # Get the application
        try:
            application = MembershipApplication.objects.get(id=application_id)
        except MembershipApplication.DoesNotExist:
            logfire.error("Application not found for notification", application_id=application_id)
            return {"status": "error", "reason": "application_not_found"}

        # Build display name and Discord mention
        name = application.display_name
        discord_mention = f"<@{application.discord_id}>"

        # Build message based on update type
        if update_type == "created":
            link = f"<{application_url}>" if application_url else ""
            message = (
                f"📝 **New Registration record**\n"
                f"{name} ({discord_mention}) joined the server. {link}"
            )
        elif update_type == "applicant_updated":
            link = f"<{application_url}>" if application_url else ""
            message = (
                f"📝 **Registration Updated**\n"
                f"{name} ({discord_mention}) updated their registration. {link}"
            )
        elif update_type == "status_changed":
            # Get human-readable status names
            old_display = _get_status_display(old_status) if old_status else "Unknown"
            new_display = _get_status_display(new_status) if new_status else "Unknown"
            admin = admin_name or "Unknown admin"
            link = f"<{application_url}>" if application_url else ""
            message = (
                f"👤 **Status Changed**\n"
                f"{admin} changed {name}'s status: {old_display} → {new_display} {link}"
            )
        elif update_type == "admin_notes":
            admin = admin_name or "Unknown admin"
            link = f"<{application_url}>" if application_url else ""
            message = (
                f"💬 **Admin Notes**\n"
                f"{admin} updated notes for {name}'s registration. {link}"
            )
        else:
            logfire.warning("Unknown update type for notification", update_type=update_type)
            return {"status": "error", "reason": "unknown_update_type"}

        # Send the message (silent for new registrations to avoid notification spam)
        silent = update_type == "created"
        success = send_discord_channel_message(channel_id, message, silent=silent)

        logfire.info(
            "Application notification sent",
            application_id=application_id,
            update_type=update_type,
            success=success,
        )

        return {
            "status": "sent" if success else "failed",
            "application_id": application_id,
            "update_type": update_type,
        }


def _get_status_display(status: str) -> str:
    """Get human-readable status name from status value.

    Args:
        status: The status code (e.g., "pending", "approved").

    Returns:
        Human-readable status name.

    """
    status_map = {
        "pending": "Pending Review",
        "in_progress": "In Progress",
        "waiting_response": "Waiting for User Response",
        "approved": "Approved",
        "rejected": "Rejected",
    }
    return status_map.get(status, status.replace("_", " ").title())
