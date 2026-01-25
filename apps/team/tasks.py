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
    changed_fields: dict | None = None,
    unchanged_fields: dict | None = None,
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
        changed_fields: Dict of {field_label: display_value} for fields that changed.
        unchanged_fields: Dict of {field_label: display_value} for fields that didn't change.

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

        # Build markdown link for application URL
        link = f"[View Record]({application_url})" if application_url else ""

        # Build message based on update type
        if update_type == "created":
            message = (
                f"📝 **New Registration record**\n"
                f"{name} ({discord_mention}) joined the server. {link}"
            )
        elif update_type == "applicant_updated":
            message = (
                f"📝 **Registration Updated**\n"
                f"{name} ({discord_mention}) updated their registration."
            )

            # Add changed fields section (marked with ✏️)
            if changed_fields:
                message += "\n\n**✏️ Changed:**"
                for label, value in changed_fields.items():
                    # Truncate long values to keep message concise
                    display = str(value)
                    if len(display) > 100:
                        display = display[:100] + "..."
                    message += f"\n• {label}: {display}"

            # Add unchanged fields section (for reference)
            if unchanged_fields:
                message += "\n\n**Unchanged:**"
                for label, value in unchanged_fields.items():
                    # Truncate long values to keep message concise
                    display = str(value)
                    if len(display) > 100:
                        display = display[:100] + "..."
                    message += f"\n• {label}: {display}"

            if link:
                message += f"\n\n{link}"
        elif update_type == "status_changed":
            # Get human-readable status names
            old_display = _get_status_display(old_status) if old_status else "Unknown"
            new_display = _get_status_display(new_status) if new_status else "Unknown"
            admin = admin_name or "Unknown admin"
            message = (
                f"👤 **Status Changed**\n"
                f"{admin} changed {name}'s status: {old_display} → {new_display} {link}"
            )
        elif update_type == "admin_notes":
            admin = admin_name or "Unknown admin"
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
        "approved": "Approved",
        "rejected": "Rejected",
    }
    return status_map.get(status, status.replace("_", " ").title())


@task
def notify_race_ready_change(
    user_id: int,
    is_now_race_ready: bool,
    changed_by_user_id: int | None = None,
    verification_type: str | None = None,
) -> dict:
    """Send notification to USER_CHANGE_LOG when user's race ready status changes.

    Args:
        user_id: ID of the user whose status changed.
        is_now_race_ready: True if user gained race ready status, False if lost.
        changed_by_user_id: ID of the admin who approved/rejected the verification.
        verification_type: Type of verification that triggered the change.

    Returns:
        dict with notification status.

    """
    from apps.accounts.models import User

    with logfire.span(
        "notify_race_ready_change",
        user_id=user_id,
        is_now_race_ready=is_now_race_ready,
    ):
        channel_id = config.USER_CHANGE_LOG

        if not channel_id or channel_id == 0:
            logfire.debug("USER_CHANGE_LOG not configured, skipping notification")
            return {"status": "skipped", "reason": "channel_not_configured"}

        # Get the user
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logfire.error("User not found for race ready notification", user_id=user_id)
            return {"status": "error", "reason": "user_not_found"}

        # Get admin who made the change
        admin_name = None
        if changed_by_user_id:
            try:
                admin = User.objects.get(pk=changed_by_user_id)
                admin_name = _get_user_display_name(admin)
            except User.DoesNotExist:
                pass

        # Build display name and Discord mention
        name = _get_user_display_name(user)
        mention = f"<@{user.discord_id}>" if user.discord_id else name

        # Build message based on status change
        if is_now_race_ready:
            emoji = "🏁"
            title = "Race Ready Status Gained"
            status_text = "is now race ready"
        else:
            emoji = "⚠️"
            title = "Race Ready Status Lost"
            status_text = "is no longer race ready"

        message = f"{emoji} **{title}**\n{name} ({mention}) {status_text}."

        if verification_type:
            message += f"\nVerification: {verification_type}"
        if admin_name:
            action = "Approved" if is_now_race_ready else "Rejected"
            message += f"\n{action} by: {admin_name}"

        success = send_discord_channel_message(channel_id, message)

        logfire.info(
            "Race ready status change notification sent",
            user_id=user_id,
            is_now_race_ready=is_now_race_ready,
            changed_by_user_id=changed_by_user_id,
            channel_id=channel_id,
            success=success,
        )

        return {"status": "sent" if success else "failed", "user_id": user_id}


def _get_user_display_name(user) -> str:
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
