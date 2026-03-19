"""Background tasks for team app."""

import time

import httpx
import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]

from apps.accounts.discord_service import send_discord_channel_message, send_discord_dm
from apps.team.models import DiscordChannel, DiscordRole, MembershipApplication, RaceReadyRecord

VERIFICATION_TYPE_LABELS = {
    "weight_full": "Weight (Full)",
    "weight_light": "Weight (Light)",
    "height": "Height",
    "power": "Power",
}


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
    from apps.accounts.discord_service import add_discord_role, remove_discord_role
    from apps.accounts.models import User

    with logfire.span(
        "notify_race_ready_change",
        user_id=user_id,
        is_now_race_ready=is_now_race_ready,
    ):
        # Get the user
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logfire.error("User not found for race ready notification", user_id=user_id)
            return {"status": "error", "reason": "user_not_found"}

        # Sync the race ready Discord role
        race_ready_role_id = config.RACE_READY_ROLE_ID
        role_synced = False
        if race_ready_role_id and race_ready_role_id != 0 and user.discord_id:
            role_id_str = str(race_ready_role_id)
            if is_now_race_ready:
                role_synced = add_discord_role(user.discord_id, role_id_str)
                if role_synced:
                    if user.discord_roles is None:
                        user.discord_roles = {}
                    user.discord_roles[role_id_str] = "Race Ready"
                    user.save(update_fields=["discord_roles"])
            else:
                role_synced = remove_discord_role(user.discord_id, role_id_str)
                if role_synced and user.discord_roles and role_id_str in user.discord_roles:
                    del user.discord_roles[role_id_str]
                    user.save(update_fields=["discord_roles"])
            logfire.info(
                "Race ready role sync",
                user_id=user_id,
                discord_id=user.discord_id,
                is_now_race_ready=is_now_race_ready,
                role_synced=role_synced,
            )

        channel_id = config.USER_CHANGE_LOG

        if not channel_id or channel_id == 0:
            logfire.debug("USER_CHANGE_LOG not configured, skipping notification")
            return {"status": "role_only", "role_synced": role_synced}

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

        return {"status": "sent" if success else "failed", "user_id": user_id, "role_synced": role_synced}


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


@task
def sync_discord_channels() -> dict:
    """Fetch guild channels from the Discord API and sync to DiscordChannel model.

    Requires DISCORD_BOT_TOKEN and GUILD_ID to be configured in constance.

    Returns:
        dict with sync results (created, updated, deleted, total).

    """
    with logfire.span("sync_discord_channels"):
        bot_token = config.DISCORD_BOT_TOKEN
        guild_id = config.GUILD_ID

        if not bot_token:
            logfire.warning("DISCORD_BOT_TOKEN not configured, skipping channel sync")
            return {"status": "skipped", "reason": "bot_token_not_configured"}

        if not guild_id:
            logfire.warning("GUILD_ID not configured, skipping channel sync")
            return {"status": "skipped", "reason": "guild_id_not_configured"}

        # Fetch channels from Discord API
        response = httpx.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=30,
        )
        response.raise_for_status()
        channels_data = response.json()

        # Build a category lookup for resolving parent names
        categories = {
            ch["id"]: ch["name"]
            for ch in channels_data
            if ch.get("type") == DiscordChannel.ChannelType.CATEGORY
        }

        received_channel_ids = set()
        created = 0
        updated = 0

        for ch in channels_data:
            channel_id = ch["id"]
            received_channel_ids.add(channel_id)

            parent_id = ch.get("parent_id") or ""
            category_name = categories.get(parent_id, "") if parent_id else ""

            _, was_created = DiscordChannel.objects.update_or_create(
                channel_id=channel_id,
                defaults={
                    "name": ch.get("name") or "",
                    "channel_type": ch.get("type", 0),
                    "position": ch.get("position", 0),
                    "category_id": parent_id,
                    "category_name": category_name,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        # Delete channels no longer in Discord
        existing_ids = set(DiscordChannel.objects.values_list("channel_id", flat=True))
        stale_ids = existing_ids - received_channel_ids
        deleted = 0
        if stale_ids:
            deleted, _ = DiscordChannel.objects.filter(channel_id__in=stale_ids).delete()

        logfire.info(
            "Discord channels synced from API",
            created=created,
            updated=updated,
            deleted=deleted,
            total_received=len(channels_data),
        )

        return {
            "status": "success",
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "total": len(channels_data),
        }


@task
def sync_discord_roles() -> dict:
    """Fetch guild roles from the Discord API and sync to DiscordRole model.

    Requires DISCORD_BOT_TOKEN and GUILD_ID to be configured in constance.

    Returns:
        dict with sync results (created, updated, deleted, total).

    """
    with logfire.span("sync_discord_roles"):
        bot_token = config.DISCORD_BOT_TOKEN
        guild_id = config.GUILD_ID

        if not bot_token:
            logfire.warning("DISCORD_BOT_TOKEN not configured, skipping role sync")
            return {"status": "skipped", "reason": "bot_token_not_configured"}

        if not guild_id:
            logfire.warning("GUILD_ID not configured, skipping role sync")
            return {"status": "skipped", "reason": "guild_id_not_configured"}

        response = httpx.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/roles",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=30,
        )
        response.raise_for_status()
        roles_data = response.json()

        received_role_ids = set()
        created = 0
        updated = 0

        for role in roles_data:
            role_id = str(role["id"])
            received_role_ids.add(role_id)

            _, was_created = DiscordRole.objects.update_or_create(
                role_id=role_id,
                defaults={
                    "name": role.get("name", ""),
                    "color": role.get("color", 0),
                    "position": role.get("position", 0),
                    "managed": role.get("managed", False),
                    "mentionable": role.get("mentionable", False),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        existing_ids = set(DiscordRole.objects.values_list("role_id", flat=True))
        stale_ids = existing_ids - received_role_ids
        deleted = 0
        if stale_ids:
            deleted, _ = DiscordRole.objects.filter(role_id__in=stale_ids).delete()

        logfire.info(
            "Discord roles synced from API",
            created=created,
            updated=updated,
            deleted=deleted,
            total_received=len(roles_data),
        )

        return {
            "status": "success",
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "total": len(roles_data),
        }


@task
def warn_expiring_verifications(days: int = 15, dry_run: bool = False) -> dict:
    """Send Discord DMs to users whose verification records expire in exactly N days.

    Args:
        days: Exact number of days until expiration to match (strict equality).
        dry_run: If True, return the list of matching users/records without sending DMs.

    Returns:
        Summary dict with status, counts, and user list.

    """
    with logfire.span("warn_expiring_verifications", days=days, dry_run=dry_run):
        verified_records = RaceReadyRecord.objects.filter(
            status=RaceReadyRecord.Status.VERIFIED,
            user__discord_id__isnull=False,
        ).exclude(user__discord_id="").select_related("user")

        total_checked = 0
        matching_records = []

        for record in verified_records:
            total_checked += 1
            remaining = record.days_remaining
            if remaining is None:
                continue
            if remaining == days:
                matching_records.append(record)

        logfire.info(
            "Expiring verification scan complete",
            days=days,
            total_checked=total_checked,
            matching=len(matching_records),
            dry_run=dry_run,
        )

        if dry_run:
            users_warned = [
                f"{_get_user_display_name(r.user)} ({r.get_verify_type_display()}, expires {r.expires_date})"
                for r in matching_records
            ]
            return {
                "status": "dry_run",
                "days": days,
                "dry_run": True,
                "total_checked": total_checked,
                "warnings_sent": 0,
                "users_warned": users_warned,
                "errors": [],
            }

        warnings_sent = 0
        users_warned = []
        errors = []

        for record in matching_records:
            user = record.user
            verify_label = record.get_verify_type_display()
            expires = record.expires_date
            expires_str = expires.strftime("%B %d, %Y") if expires else "unknown"

            message = (
                f"\u23f0 **Verification Expiring Soon**\n\n"
                f"Your **{verify_label}** verification expires in **{days} days** ({expires_str}).\n\n"
                f"Please submit a new verification record to maintain your Race Ready status."
            )

            success = send_discord_dm(user.discord_id, message)
            if success:
                warnings_sent += 1
                users_warned.append(_get_user_display_name(user))
                logfire.info(
                    "Expiring verification DM sent",
                    user_id=user.id,
                    discord_id=user.discord_id,
                    verify_type=record.verify_type,
                    days_remaining=days,
                )
            else:
                errors.append(f"Failed to DM {_get_user_display_name(user)} ({user.discord_id})")
                logfire.warning(
                    "Failed to send expiring verification DM",
                    user_id=user.id,
                    discord_id=user.discord_id,
                    verify_type=record.verify_type,
                )

            time.sleep(0.5)

        return {
            "status": "complete",
            "days": days,
            "dry_run": False,
            "total_checked": total_checked,
            "warnings_sent": warnings_sent,
            "users_warned": users_warned,
            "errors": errors,
        }


@task
def notify_captains_verification(
    user_id: int,
    record_id: int,
    notification_type: str,
) -> dict:
    """Notify squad captains/vice-captains when a member's verification record changes.

    Sends Discord DMs to captains of squads (with captain_notifications=True) in
    current/upcoming events where the user is a squad member.

    Args:
        user_id: ID of the user whose verification record changed.
        record_id: ID of the RaceReadyRecord.
        notification_type: One of "submitted", "verified", "rejected".

    Returns:
        dict with notification status and counts.

    """
    from apps.accounts.models import User
    from apps.events.models import SquadMember

    with logfire.span(
        "notify_captains_verification",
        user_id=user_id,
        record_id=record_id,
        notification_type=notification_type,
    ):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logfire.error("User not found for captain notification", user_id=user_id)
            return {"status": "error", "reason": "user_not_found"}

        try:
            record = RaceReadyRecord.objects.get(pk=record_id)
        except RaceReadyRecord.DoesNotExist:
            logfire.error("RaceReadyRecord not found for captain notification", record_id=record_id)
            return {"status": "error", "reason": "record_not_found"}

        from django.utils import timezone as tz

        today = tz.now().date()

        # Find squads where this user is a member in active/upcoming events with notifications on
        squad_memberships = (
            SquadMember.objects.filter(
                user=user,
                status=SquadMember.Status.MEMBER,
                squad__captain_notifications=True,
                squad__event__visible=True,
                squad__event__end_date__gte=today,
            )
            .select_related("squad__captain", "squad__vice_captain", "squad__event")
        )

        # Collect unique captains (deduplicate, skip None, skip the user themselves)
        captains_to_notify: dict[int, User] = {}
        for membership in squad_memberships:
            squad = membership.squad
            for leader in (squad.captain, squad.vice_captain):
                if leader and leader.pk != user.pk and leader.discord_id and leader.pk not in captains_to_notify:
                    captains_to_notify[leader.pk] = leader

        if not captains_to_notify:
            logfire.info(
                "No captains to notify for verification change",
                user_id=user_id,
                record_id=record_id,
                notification_type=notification_type,
            )
            return {"status": "no_captains", "notified": 0}

        # Build message
        user_name = _get_user_display_name(user)
        verify_label = VERIFICATION_TYPE_LABELS.get(record.verify_type, record.verify_type)

        if notification_type == "submitted":
            message = f"{user_name} submitted a **{verify_label}** verification record (pending review)."
        elif notification_type == "verified":
            message = f"{user_name}'s **{verify_label}** verification has been approved."
        elif notification_type == "rejected":
            message = f"{user_name}'s **{verify_label}** verification has been rejected."
        else:
            logfire.warning("Unknown notification_type for captain verification", notification_type=notification_type)
            return {"status": "error", "reason": "unknown_notification_type"}

        # Send DMs
        sent = 0
        errors = []
        for captain in captains_to_notify.values():
            success = send_discord_dm(captain.discord_id, message)
            if success:
                sent += 1
                logfire.info(
                    "Captain verification DM sent",
                    captain_id=captain.id,
                    captain_discord_id=captain.discord_id,
                    user_id=user_id,
                    notification_type=notification_type,
                )
            else:
                errors.append(f"Failed to DM {_get_user_display_name(captain)} ({captain.discord_id})")
                logfire.warning(
                    "Failed to send captain verification DM",
                    captain_id=captain.id,
                    captain_discord_id=captain.discord_id,
                )
            time.sleep(0.5)

        return {
            "status": "complete",
            "notified": sent,
            "total_captains": len(captains_to_notify),
            "errors": errors,
        }
