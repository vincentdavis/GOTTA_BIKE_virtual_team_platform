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


@task
def sync_zr_category_roles() -> dict:
    """Sync Zwift Racing category Discord roles for all users.

    For each user with a discord_id, looks up their ZRRider record to determine
    their current ZR category, then ensures the correct Discord role is assigned.
    Removes any stale ZR category roles and posts upgrade announcements.

    Returns:
        dict with sync results summary.

    """
    from apps.zwiftracing.models import ZRRider

    # Category order from highest to lowest
    ZR_CATEGORY_ORDER = [
        "Diamond",
        "Ruby",
        "Emerald",
        "Sapphire",
        "Amethyst",
        "Platinum",
        "Gold",
        "Silver",
        "Bronze",
        "Copper",
    ]

    def _get_zr_role_config() -> dict[str, str]:
        """Return mapping of category name to role ID string, excluding unconfigured (0) entries.

        Returns:
            Dict mapping category name to role ID string.

        """
        role_map = {
            "Diamond": config.ZR_ROLE_DIAMOND,
            "Ruby": config.ZR_ROLE_RUBY,
            "Emerald": config.ZR_ROLE_EMERALD,
            "Sapphire": config.ZR_ROLE_SAPPHIRE,
            "Amethyst": config.ZR_ROLE_AMETHYST,
            "Platinum": config.ZR_ROLE_PLATINUM,
            "Gold": config.ZR_ROLE_GOLD,
            "Silver": config.ZR_ROLE_SILVER,
            "Bronze": config.ZR_ROLE_BRONZE,
            "Copper": config.ZR_ROLE_COPPER,
        }
        return {cat: str(rid) for cat, rid in role_map.items() if rid and rid != 0}

    with logfire.span("sync_zr_category_roles"):
        role_config = _get_zr_role_config()
        if not role_config:
            logfire.info("No ZR category roles configured, skipping sync")
            return {"status": "skipped", "reason": "no_roles_configured"}

        # All configured ZR role IDs (for detecting stale roles)
        all_zr_role_ids = set(role_config.values())
        unassigned_role_id = str(config.ZR_ROLE_UNASSIGNED) if config.ZR_ROLE_UNASSIGNED else None
        if unassigned_role_id and unassigned_role_id != "0":
            all_zr_role_ids.add(unassigned_role_id)
        else:
            unassigned_role_id = None

        # Reverse lookup: role_id → category name
        role_id_to_category = {rid: cat for cat, rid in role_config.items()}

        # Build ZRRider lookup by zwid
        suffix = config.ZR_CATEGORY_SUFFIX or ""

        users_with_discord = User.objects.exclude(discord_id="").exclude(discord_id__isnull=True)
        total_users = users_with_discord.count()

        logfire.info("Starting ZR category role sync", total_users=total_users, configured_roles=len(role_config))

        added = 0
        removed = 0
        upgraded = 0
        unchanged = 0
        errors = 0

        upgrade_channel_id = config.ZR_UPGRADE_NOTICE_CHANNEL

        for user in users_with_discord.iterator():
            try:
                # Determine desired category
                desired_category = None
                if user.zwid:
                    try:
                        zr_rider = ZRRider.objects.get(zwid=user.zwid)
                        desired_category = zr_rider.race_current_category or None
                    except ZRRider.DoesNotExist:
                        pass

                # Determine desired role ID
                if desired_category and desired_category in role_config:
                    desired_role_id = role_config[desired_category]
                elif unassigned_role_id:
                    desired_role_id = unassigned_role_id
                    desired_category = None
                else:
                    desired_role_id = None

                # Find current ZR roles the user has
                current_roles = user.discord_roles or {}
                current_zr_role_ids = set(current_roles.keys()) & all_zr_role_ids

                # Determine old category from current roles
                old_category = None
                for rid in current_zr_role_ids:
                    if rid in role_id_to_category:
                        old_category = role_id_to_category[rid]
                        break

                # Check if already correct
                if desired_role_id and current_zr_role_ids == {desired_role_id}:
                    unchanged += 1
                    continue

                if not desired_role_id and not current_zr_role_ids:
                    unchanged += 1
                    continue

                # Remove stale ZR roles
                roles_to_remove = current_zr_role_ids - ({desired_role_id} if desired_role_id else set())
                for old_role_id in roles_to_remove:
                    success = remove_discord_role(user.discord_id, old_role_id)
                    if success:
                        removed += 1
                        if old_role_id in current_roles:
                            del current_roles[old_role_id]
                    else:
                        errors += 1
                    time.sleep(0.5)

                # Add new role if not already present
                if desired_role_id and desired_role_id not in current_zr_role_ids:
                    role_name = desired_category or "Unassigned"
                    if suffix:
                        role_name = f"{role_name} {suffix}"
                    success = add_discord_role(user.discord_id, desired_role_id)
                    if success:
                        added += 1
                        current_roles[desired_role_id] = role_name
                    else:
                        errors += 1
                    time.sleep(0.5)

                # Save updated discord_roles
                user.discord_roles = current_roles
                user.save(update_fields=["discord_roles"])

                # Check for upgrade
                if (
                    desired_category
                    and old_category
                    and desired_category != old_category
                    and desired_category in ZR_CATEGORY_ORDER
                    and old_category in ZR_CATEGORY_ORDER
                ):
                    new_index = ZR_CATEGORY_ORDER.index(desired_category)
                    old_index = ZR_CATEGORY_ORDER.index(old_category)
                    if new_index < old_index:
                        upgraded += 1
                        # Post upgrade announcement
                        if upgrade_channel_id and upgrade_channel_id != 0:
                            name = _get_user_display_name(user)
                            mention = f"<@{user.discord_id}>"
                            message = (
                                f"⬆️ **ZR Category Upgrade**\n"
                                f"{name} ({mention}) upgraded from **{old_category}** to **{desired_category}**!"
                            )
                            send_discord_channel_message(upgrade_channel_id, message)
                            time.sleep(0.2)

            except Exception as e:
                errors += 1
                logfire.error(
                    "Error syncing ZR category role for user",
                    user_id=user.id,
                    discord_id=user.discord_id,
                    error=str(e),
                )

        result = {
            "status": "completed",
            "total_users": total_users,
            "added": added,
            "removed": removed,
            "upgraded": upgraded,
            "unchanged": unchanged,
            "errors": errors,
        }

        logfire.info("ZR category role sync completed", **result)

        return result


@task
def sync_youtube_channel_ids() -> dict:
    """Extract YouTube channel IDs from user YouTube URLs.

    Finds users who have a youtube_channel URL but no youtube_channel_id,
    then extracts the channel ID from the URL by fetching the page.

    Returns:
        dict with sync results summary.

    """
    from apps.accounts.utils import extract_youtube_channel_id

    with logfire.span("sync_youtube_channel_ids"):
        # Find users with YouTube URL but no channel ID
        users_to_process = User.objects.filter(
            youtube_channel__isnull=False,
        ).exclude(
            youtube_channel="",
        ).filter(
            youtube_channel_id="",
        )

        total = users_to_process.count()
        logfire.info("Starting YouTube channel ID sync", users_to_process=total)

        success = 0
        failed = 0
        users_updated = []

        for user in users_to_process.iterator():
            channel_id = extract_youtube_channel_id(user.youtube_channel)

            if channel_id:
                user.youtube_channel_id = channel_id
                user.save(update_fields=["youtube_channel_id"])
                success += 1
                users_updated.append(user.id)
                logfire.debug(
                    "Extracted YouTube channel ID",
                    user_id=user.id,
                    youtube_url=user.youtube_channel,
                    channel_id=channel_id,
                )
            else:
                failed += 1
                logfire.warning(
                    "Failed to extract YouTube channel ID",
                    user_id=user.id,
                    youtube_url=user.youtube_channel,
                )

            # Rate limit: avoid hammering YouTube
            time.sleep(1)

        result = {
            "status": "completed",
            "total_processed": total,
            "success": success,
            "failed": failed,
            "users_updated": users_updated,
        }

        logfire.info("YouTube channel ID sync completed", **result)

        return result


@task
def sync_youtube_videos() -> dict:
    """Fetch new videos from YouTube RSS feeds for all users with channel IDs.

    Fetches videos from each user's YouTube channel RSS feed and stores
    new videos in the database.

    Returns:
        dict with sync results summary.

    """
    from apps.accounts.models import YouTubeVideo
    from apps.accounts.utils import fetch_youtube_videos

    with logfire.span("sync_youtube_videos"):
        # Find users with YouTube channel IDs
        users_with_channels = User.objects.filter(
            youtube_channel_id__isnull=False,
        ).exclude(
            youtube_channel_id="",
        )

        total_users = users_with_channels.count()
        logfire.info("Starting YouTube video sync", users_to_process=total_users)

        total_new_videos = 0
        users_processed = 0
        errors = 0

        for user in users_with_channels.iterator():
            try:
                # Fetch videos from RSS
                videos = fetch_youtube_videos(user.youtube_channel_id, limit=10)

                new_count = 0
                for video_data in videos:
                    # Create or update video record
                    video, created = YouTubeVideo.objects.update_or_create(
                        user=user,
                        video_id=video_data["video_id"],
                        defaults={
                            "title": video_data["title"],
                            "thumbnail_url": video_data.get("thumbnail", ""),
                            "published_at": video_data.get("published"),
                        },
                    )
                    if created:
                        new_count += 1

                total_new_videos += new_count
                users_processed += 1

                logfire.debug(
                    "Synced YouTube videos for user",
                    user_id=user.id,
                    channel_id=user.youtube_channel_id,
                    videos_fetched=len(videos),
                    new_videos=new_count,
                )

            except Exception as e:
                errors += 1
                logfire.error(
                    "Error syncing YouTube videos for user",
                    user_id=user.id,
                    channel_id=user.youtube_channel_id,
                    error=str(e),
                )

            # Rate limit: avoid hammering YouTube
            time.sleep(1)

        result = {
            "status": "completed",
            "total_users": total_users,
            "users_processed": users_processed,
            "new_videos": total_new_videos,
            "errors": errors,
        }

        logfire.info("YouTube video sync completed", **result)

        return result
