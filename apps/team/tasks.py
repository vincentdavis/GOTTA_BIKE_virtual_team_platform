"""Background tasks for team app."""

import json
import time

import httpx
import logfire
from constance import config
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

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


def _expiry_sentence(verify_label: str, remaining: int, expires_str: str) -> str:
    """Phrase the headline for the number of days actually remaining.

    The old wording was ``f"expires in **{remaining} days**"`` unconditionally, which reads
    "expires in 1 days" on the one-day warning and "in 0 days" on the day it lapses -- exactly
    the two most urgent messages a rider gets.

    Args:
        verify_label: Display name of the verification type.
        remaining: Days until expiry. Zero means today.
        expires_str: Formatted expiry date.

    Returns:
        The headline sentence.

    """
    if remaining == 0:
        return f"Your **{verify_label}** verification expires **today** ({expires_str})."
    day_word = "day" if remaining == 1 else "days"
    return f"Your **{verify_label}** verification expires in **{remaining} {day_word}** ({expires_str})."


def _threshold_due(remaining: int, already_warned: int | None, thresholds: list[int]) -> int | None:
    """Return the warning threshold this record is due, or None if it owes nothing.

    "Due" means crossed but not yet served. A record at 14 days with thresholds [15, 7, 3, 1]
    and nothing warned yet is due the 15-day warning -- the day it was exactly 15 has passed,
    and under the old exact-equality test that warning was simply lost.

    Thresholds are served largest-first and only once each, so a rider gets at most one DM per
    threshold no matter how many runs land, and a rider nobody could reach for a week still
    gets the most urgent warning they have earned rather than nothing.

    Args:
        remaining: Days until this record expires. May be negative (already lapsed).
        already_warned: Lowest threshold previously served, or None if never warned.
        thresholds: The configured EXPIRE_WARNING_DAYS values.

    Returns:
        The threshold to warn about now, or None.

    """
    # Crossed = the rider has this much time left or less.
    crossed = [t for t in thresholds if remaining <= t]
    if not crossed:
        return None
    due = min(crossed)
    if already_warned is not None and due >= already_warned:
        # Already served this one, or a more urgent one.
        return None
    return due


@task
def warn_expiring_verifications(days: int | list[int] | None = None, dry_run: bool = False) -> dict:
    """Send Discord DMs to users whose verification records expire in exactly N days.

    Args:
        days: Exact number(s) of days until expiration to match. May be a single
            int (legacy), a list of ints, or ``None`` to read the
            ``EXPIRE_WARNING_DAYS`` Constance setting (JSON list of ints).
        dry_run: If True, return the list of matching users/records without sending DMs.

    Returns:
        Summary dict with status, counts, and user list.

    """
    from apps.team.services import expiry_warning_thresholds

    if days is None:
        # Shared with the banner (services.expiry_warning_thresholds) so the two surfaces
        # cannot drift apart the way they had.
        days_list = expiry_warning_thresholds()
    elif isinstance(days, int):
        days_list = [days]
    else:
        days_list = [int(d) for d in days]

    today = timezone.now().date()

    with logfire.span("warn_expiring_verifications", days=days_list, dry_run=dry_run):
        verified_records = RaceReadyRecord.objects.filter(
            status=RaceReadyRecord.Status.VERIFIED,
            user__discord_id__isnull=False,
        ).exclude(user__discord_id="").select_related("user")

        total_checked = 0
        # List of (record, days_remaining) so the DM can quote the actual threshold hit.
        # (record, days_remaining, threshold_being_served)
        matching_records: list[tuple[RaceReadyRecord, int, int]] = []
        # Per-user map of all verified records with a meaningful days_remaining, used
        # to enrich the DM with the user's other verifications.
        verified_by_user: dict[int, list[tuple[RaceReadyRecord, int]]] = {}
        skipped_already_warned = 0
        skipped_opted_out = 0

        from collections import defaultdict

        from apps.team.services import covering_records_by_type, is_expiring_soon

        records_by_user: dict[int, list[RaceReadyRecord]] = defaultdict(list)
        for record in verified_records:
            total_checked += 1
            records_by_user[record.user_id].append(record)

        # Reconcile per verify_type before warning: only each type's longest-lived
        # record is a warning candidate, so a record the rider has already renewed
        # (a newer same-type record with more days left) never triggers a nag while
        # coverage still stands. Keeps this task in lockstep with the web banner.
        for user_id, user_records in records_by_user.items():
            for record in covering_records_by_type(user_records).values():
                remaining = record.days_remaining
                if remaining is None:
                    continue
                verified_by_user.setdefault(user_id, []).append((record, remaining))

                # Warn on the highest threshold this record has CROSSED but not yet been
                # warned about -- not on exact equality with today's days_remaining.
                #
                # The exact test gave each threshold a single calendar day, and the job runs
                # once a day on an interval anchored at process boot, so every deploy shifts
                # the slot and some days get no run at all. A rider sitting on 15 days that
                # day lost that warning permanently; nothing recorded that one was owed. This
                # makes any later run a catch-up: the 15-day warning still goes out on day 14.
                # A lapsed record is expired, not expiring. Without this the catch-up
                # would serve it the 0-day threshold and DM "expires in -4 days".
                if not is_expiring_soon(remaining):
                    continue
                due = _threshold_due(remaining, record.last_warned_threshold, days_list)
                if due is None:
                    continue
                if record.last_warned_at == today:
                    skipped_already_warned += 1
                    continue
                # send_discord_dm returns True for an opted-out member -- deliberately, so
                # callers do not retry forever -- which meant they were counted in
                # warnings_sent and listed as warned. The number an admin reads should mean
                # "DMs delivered", so they are counted separately and never attempted.
                if record.user.discord_dm_opt_out:
                    skipped_opted_out += 1
                    continue
                matching_records.append((record, remaining, due))

        logfire.info(
            "Expiring verification scan complete",
            days=days_list,
            total_checked=total_checked,
            matching=len(matching_records),
            skipped_already_warned=skipped_already_warned,
            skipped_opted_out=skipped_opted_out,
            dry_run=dry_run,
        )

        if dry_run:
            users_warned = [
                f"{_get_user_display_name(r.user)} ({r.get_verify_type_display()}, "
                f"{remaining}d remaining, expires {r.expires_date}, "
                f"serving the {due}-day warning)"
                for r, remaining, due in matching_records
            ]
            return {
                "status": "dry_run",
                "days": days_list,
                "dry_run": True,
                "total_checked": total_checked,
                "warnings_sent": 0,
                "skipped_opted_out": skipped_opted_out,
                "users_warned": users_warned,
                "errors": [],
            }

        warnings_sent = 0
        users_warned = []
        errors = []

        for record, remaining, due in matching_records:
            user = record.user
            verify_label = record.get_verify_type_display()
            expires = record.expires_date
            expires_str = expires.strftime("%B %d, %Y") if expires else "unknown"

            lines = [
                "\u23f0 **Verification Expiring Soon**",
                "",
                _expiry_sentence(verify_label, remaining, expires_str),
            ]
            others = sorted(
                (
                    (r, rem)
                    for (r, rem) in verified_by_user.get(record.user_id, [])
                    if r.pk != record.pk and rem >= 0
                ),
                key=lambda item: item[1],
            )
            if others:
                lines.append("")
                lines.append("Your other verifications:")
                for r, rem in others:
                    r_label = r.get_verify_type_display()
                    r_expires = r.expires_date.strftime("%B %d, %Y") if r.expires_date else "unknown"
                    day_word = "day" if rem == 1 else "days"
                    lines.append(f"\u2022 **{r_label}**: {rem} {day_word} remaining ({r_expires})")
            lines.append("")
            lines.append("Please submit a new verification record to maintain your Race Ready status.")
            message = "\n".join(lines)

            # One rider must not cost the rest of the batch their only warning. send_discord_dm
            # returns False for handled errors, but an unhandled one (a DNS failure, a bad
            # token) previously aborted the loop, skipping every rider after this point --
            # always the same riders, since the iteration order is stable.
            try:
                success = send_discord_dm(user.discord_id, message)
            except Exception as exc:
                success = False
                logfire.error(
                    "Expiring verification DM raised",
                    user_id=user.id,
                    discord_id=user.discord_id,
                    verify_type=record.verify_type,
                    record_id=record.pk,
                    error=str(exc),
                )

            if success:
                record.last_warned_at = today
                # Stamping the threshold, not just the date, is what makes a later run a
                # catch-up rather than a duplicate: it records WHICH warning this rider has
                # had, so the next run serves the next one down and never repeats this one.
                record.last_warned_threshold = due
                record.save(update_fields=["last_warned_at", "last_warned_threshold"])
                warnings_sent += 1
                users_warned.append(_get_user_display_name(user))
                logfire.info(
                    "Expiring verification DM sent",
                    user_id=user.id,
                    discord_id=user.discord_id,
                    verify_type=record.verify_type,
                    days_remaining=remaining,
                    threshold=due,
                )
            else:
                errors.append(f"Failed to DM {_get_user_display_name(user)} ({user.discord_id})")
                logfire.warning(
                    "Failed to send expiring verification DM",
                    user_id=user.id,
                    discord_id=user.discord_id,
                    verify_type=record.verify_type,
                    record_id=record.pk,
                    threshold=due,
                )
            time.sleep(0.5)

        return {
            "status": "complete",
            "days": days_list,
            "dry_run": False,
            "total_checked": total_checked,
            "warnings_sent": warnings_sent,
            "skipped_opted_out": skipped_opted_out,
            "users_warned": users_warned,
            "errors": errors,
        }


@task
def purge_expired_media() -> dict:
    """Strip evidence from verification records that have expired or aged out.

    Scheduled daily. Uploaded photos and videos of riders on scales otherwise sit in
    storage until an admin remembers to click the manual purge button, which is what this
    replaces as the routine path -- the button stays for purging on demand.

    Two sweeps, because they answer different questions. The first removes evidence whose
    verification has expired. The second removes evidence we have simply held too long,
    whatever its type -- which is what catches height, where the verification is valid
    forever and so the photograph used to be kept forever too.

    Returns:
        Counts of records ``considered``, ``purged`` and ``failed``, summed across both
        sweeps, plus each sweep's own counts under ``expired`` and ``aged``.

    """
    from apps.team.services import purge_aged_verification_media, purge_expired_verification_media

    expired = purge_expired_verification_media()
    aged = purge_aged_verification_media()
    return {
        "considered": expired["considered"] + aged["considered"],
        "purged": expired["purged"] + aged["purged"],
        "failed": expired["failed"] + aged["failed"],
        "expired": expired,
        "aged": aged,
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
            .select_related("squad__event")
            .prefetch_related("squad__captains", "squad__vice_captains")
        )

        # Collect unique captains (deduplicate, skip the user themselves)
        captains_to_notify: dict[int, User] = {}
        for membership in squad_memberships:
            squad = membership.squad
            for leader in (*squad.captains.all(), *squad.vice_captains.all()):
                if leader.pk != user.pk and leader.discord_id and leader.pk not in captains_to_notify:
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


def _verification_record_url(record_id: int) -> str:
    """Build the best available URL to a verification record's review page.

    Uses the first non-wildcard, non-localhost ALLOWED_HOSTS entry to form an
    absolute https URL; falls back to the relative path when no public host is set.

    Args:
        record_id: ID of the RaceReadyRecord.

    Returns:
        Absolute (or relative, as fallback) URL string.

    """
    from django.urls import reverse

    from gotta_bike_platform.config import settings as app_settings

    path = reverse("team:verification_record_detail", args=[record_id])
    non_public_hosts = {"*", "localhost", "127.0.0.1", "0.0.0.0"}  # noqa: S104 — exclusion list, not a bind address
    host = next((h for h in app_settings.allowed_hosts if h not in non_public_hosts), None)
    return f"https://{host}{path}" if host else path


@task
def notify_pvt_power_submission(record_id: int) -> dict:
    """DM the performance verification team when a power verification record is submitted.

    Sends a Discord DM to every member holding a role in
    ``PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES`` (or with an explicit
    ``performance_verification_team`` override), excluding the submitter.

    Args:
        record_id: ID of the submitted power RaceReadyRecord.

    Returns:
        dict with notification status and counts.

    """
    from django.db.models import Q

    from apps.accounts.models import Permissions, User

    with logfire.span("notify_pvt_power_submission", record_id=record_id):
        try:
            record = RaceReadyRecord.objects.select_related("user").get(pk=record_id)
        except RaceReadyRecord.DoesNotExist:
            logfire.error("RaceReadyRecord not found for PVT power notification", record_id=record_id)
            return {"status": "error", "reason": "record_not_found"}

        if record.verify_type != "power":
            logfire.warning(
                "PVT power notification skipped for non-power record",
                record_id=record_id,
                verify_type=record.verify_type,
            )
            return {"status": "skipped", "reason": "not_power"}

        # Resolve PVT Discord role IDs from Constance
        try:
            role_ids = [int(r) for r in json.loads(config.PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES or "[]")]
        except (json.JSONDecodeError, ValueError, TypeError):
            logfire.warning("Invalid PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES setting")
            role_ids = []

        # Candidate users: hold one of the PVT roles, or have an explicit override granting it
        candidate_q = Q(permission_overrides__performance_verification_team=True)
        for rid in role_ids:
            candidate_q |= Q(discord_roles__has_key=str(rid))

        candidates = User.objects.filter(candidate_q).exclude(discord_id="").exclude(discord_id__isnull=True)

        # Dedupe, honor full permission semantics (overrides can revoke), skip the submitter
        recipients: dict[int, User] = {}
        for candidate in candidates:
            if candidate.pk == record.user_id or not candidate.discord_id:
                continue
            if candidate.has_permission(Permissions.PERFORMANCE_VERIFICATION_TEAM):
                recipients[candidate.pk] = candidate

        if not recipients:
            logfire.info("No PVT members to notify for power submission", record_id=record_id)
            return {"status": "no_recipients", "notified": 0}

        submitter = _get_user_display_name(record.user)
        message = (
            f"⚡ **{submitter}** submitted a **Power** verification record for review.\n"
            f"Review it here: {_verification_record_url(record_id)}"
        )

        sent = 0
        errors = []
        for recipient in recipients.values():
            if send_discord_dm(recipient.discord_id, message):
                sent += 1
                logfire.info(
                    "PVT power DM sent",
                    recipient_id=recipient.id,
                    recipient_discord_id=recipient.discord_id,
                    record_id=record_id,
                )
            else:
                errors.append(recipient.discord_id)
                logfire.warning(
                    "Failed to send PVT power DM",
                    recipient_id=recipient.id,
                    recipient_discord_id=recipient.discord_id,
                    record_id=record_id,
                )
            time.sleep(0.5)

        logfire.info(
            "PVT power notification complete",
            record_id=record_id,
            notified=sent,
            failed=len(errors),
            total_recipients=len(recipients),
        )
        return {"status": "complete", "notified": sent, "errors": errors, "total_recipients": len(recipients)}
