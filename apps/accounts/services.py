"""Service functions for accounts app."""

from __future__ import annotations

import contextlib
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import logfire
from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.team.models import MembershipApplication

DISCORD_API_BASE = "https://discord.com/api/v10"
GUILD_MEMBER_PAGE_SIZE = 1000


def get_approved_application(discord_id: str) -> MembershipApplication | None:
    """Find approved MembershipApplication matching discord_id.

    Args:
        discord_id: The Discord user ID to match.

    Returns:
        The approved MembershipApplication if found, None otherwise.

    """
    from apps.team.models import MembershipApplication

    if not discord_id:
        return None

    try:
        return MembershipApplication.objects.get(
            discord_id=discord_id,
            status=MembershipApplication.Status.APPROVED,
        )
    except MembershipApplication.DoesNotExist:
        return None


# Field mapping from MembershipApplication to User
# Format: (application_field, user_field, label, transform_func)
FIELD_MAPPING: list[tuple[str, str, str, Callable | None]] = [
    ("first_name", "first_name", "First Name", None),
    ("last_name", "last_name", "Last Name", None),
    ("email", "email", "Email", None),
    ("zwift_id", "zwid", "Zwift ID", lambda v: int(v) if v else None),
    ("zwift_verified", "zwid_verified", "Zwift Verified", None),
    ("country", "country", "Country", None),
    ("timezone", "timezone", "Timezone", None),
    ("birth_year", "birth_year", "Birth Year", None),
    ("gender", "gender", "Gender", None),
    ("unit_preference", "unit_preference", "Unit Preference", None),
    ("trainer", "trainer", "Trainer", None),
    ("power_meter", "powermeter", "Power Meter", None),
    ("dual_recording", "dual_recording", "Dual Recording", None),
    ("heartrate_monitor", "heartrate_monitor", "Heart Rate Monitor", None),
    ("strava_profile", "strava_url", "Strava Profile", None),
    ("tpv_profile_url", "tpv_profile_url", "TPV Profile URL", None),
]


def _format_display_value(value: Any, field_name: str) -> str:
    """Format a value for display in the import preview.

    Args:
        value: The value to format.
        field_name: The field name for context.

    Returns:
        Human-readable string representation.

    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field_name == "country" and value:
        # CountryField returns a Country object with a name attribute
        return str(value.name) if hasattr(value, "name") else str(value)
    return str(value)


def get_importable_fields(application: MembershipApplication) -> dict[str, dict]:
    """Return importable fields from application with their values.

    Args:
        application: The MembershipApplication to extract fields from.

    Returns:
        Dictionary mapping field names to {label, value, display_value, user_field}.

    """
    result = {}

    for app_field, user_field, label, transform in FIELD_MAPPING:
        value = getattr(application, app_field, None)

        # Skip empty values
        if value is None or value == "":
            continue

        # Transform the value if needed
        transformed_value = transform(value) if transform else value

        # Skip if transform returns None
        if transformed_value is None:
            continue

        result[app_field] = {
            "label": label,
            "value": transformed_value,
            "display_value": _format_display_value(value, app_field),
            "user_field": user_field,
        }

    return result


def import_application_to_user(user: User, application: MembershipApplication) -> list[str]:
    """Copy fields from application to user.

    Args:
        user: The User to update.
        application: The MembershipApplication to copy from.

    Returns:
        List of imported field names.

    """
    imported_fields = []
    update_fields = []

    for app_field, user_field, label, transform in FIELD_MAPPING:
        app_value = getattr(application, app_field, None)

        # Skip empty values
        if app_value is None or app_value == "":
            continue

        # Get current user value
        user_value = getattr(user, user_field, None)

        # Skip if user already has a value (don't overwrite)
        if user_value is not None and user_value != "" and user_value is not False:
            # Special handling for boolean fields - False is a valid value
            if isinstance(user_value, bool) and user_value is False:
                pass  # Continue to potentially overwrite
            else:
                continue

        # Transform the value if needed
        final_value = transform(app_value) if transform else app_value

        # Skip if transform returns None
        if final_value is None:
            continue

        # Set the value
        setattr(user, user_field, final_value)
        imported_fields.append(label)
        update_fields.append(user_field)

    if update_fields:
        user.save(update_fields=update_fields)
        logfire.info(
            "Imported application data to user profile",
            user_id=user.id,
            discord_id=user.discord_id,
            application_id=str(application.id),
            imported_fields=imported_fields,
        )

    return imported_fields


def fetch_guild_members_from_discord(guild_id: str | int, bot_token: str) -> list[dict[str, Any]]:
    """Pull the full guild-member roster from Discord's REST API.

    Iterates ``GET /guilds/{id}/members`` with ``after`` pagination until the
    final page is reached, transparently retrying on 429s by honoring Discord's
    ``retry_after`` body. Returns each member in the normalized dict shape that
    :func:`apply_guild_member_sync` consumes, so the platform-side fetcher and
    the bot's inbound POST share the same downstream pipeline.

    Args:
        guild_id: The Discord guild ID to fetch members from.
        bot_token: A Discord bot token with the ``GUILD_MEMBERS`` privileged intent.

    Returns:
        List of normalized member dicts. Raises ``httpx.HTTPStatusError`` on
        any non-429 error response from Discord.

    """
    headers = {"Authorization": f"Bot {bot_token}"}
    members: list[dict[str, Any]] = []
    after: str | None = None

    with httpx.Client(timeout=30.0) as client:
        while True:
            params: dict[str, str | int] = {"limit": GUILD_MEMBER_PAGE_SIZE}
            if after:
                params["after"] = after

            response = client.get(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/members",
                headers=headers,
                params=params,
            )
            if response.status_code == 429:
                retry_after = float(response.json().get("retry_after", 1.0))
                logfire.warning(
                    "Discord rate limit on guild members fetch",
                    retry_after=retry_after,
                    fetched_so_far=len(members),
                )
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            page = response.json()
            if not page:
                break

            for raw in page:
                user = raw.get("user") or {}
                members.append(
                    {
                        "discord_id": str(user.get("id", "")),
                        "username": user.get("username") or "",
                        "display_name": user.get("global_name") or "",
                        "nickname": raw.get("nick") or "",
                        "avatar_hash": user.get("avatar") or "",
                        "roles": list(raw.get("roles") or []),
                        "joined_at": raw.get("joined_at"),
                        "is_bot": bool(user.get("bot", False)),
                    }
                )

            if len(page) < GUILD_MEMBER_PAGE_SIZE:
                break
            after = str(page[-1].get("user", {}).get("id", ""))
            if not after:
                break

    return members


def apply_guild_member_sync(members: list[dict[str, Any]], *, source: str = "unknown") -> dict[str, int]:
    """Reconcile the GuildMember table against an authoritative member list.

    Members present in ``members`` are upserted (and linked to a ``User`` by
    ``discord_id`` when possible); members previously active but missing from
    the input are marked left and trigger a low-priority Membership ticket via
    :func:`apps.tickets.services.create_member_left_ticket`. Idempotent: re-running
    with the same input is a no-op apart from refreshing ``date_modified``.

    Args:
        members: Normalized list of member dicts (see :func:`fetch_guild_members_from_discord`).
        source: Free-form label captured in the audit log to identify which
            caller drove the sync (``"discord_api"``, ``"bot_webhook"``, etc.).

    Returns:
        Dict with ``created``, ``updated``, ``rejoined``, ``left``, ``linked``,
        ``total_received``, and ``total_active`` counts.

    """
    # Local imports avoid a circular dependency at module import time.
    from apps.accounts.models import GuildMember, User
    from apps.tickets.services import create_member_left_ticket

    received_discord_ids = {m["discord_id"] for m in members if m.get("discord_id")}
    existing_discord_ids = set(
        GuildMember.objects.filter(date_left__isnull=True).values_list("discord_id", flat=True)
    )
    users_by_discord_id = {u.discord_id: u for u in User.objects.filter(discord_id__in=received_discord_ids)}

    created = 0
    updated = 0
    rejoined = 0
    linked = 0

    for member_data in members:
        joined_at: datetime | None = None
        raw_joined = member_data.get("joined_at")
        if raw_joined:
            with contextlib.suppress(ValueError):
                joined_at = datetime.fromisoformat(str(raw_joined).replace("Z", "+00:00"))

        existing = GuildMember.objects.filter(discord_id=member_data["discord_id"]).first()

        if existing:
            was_left = existing.date_left is not None
            existing.username = member_data.get("username", "")
            existing.display_name = member_data.get("display_name") or ""
            existing.nickname = member_data.get("nickname") or ""
            existing.avatar_hash = member_data.get("avatar_hash") or ""
            existing.roles = member_data.get("roles") or []
            existing.joined_at = joined_at
            existing.is_bot = bool(member_data.get("is_bot", False))
            existing.date_left = None  # Clear when they're back

            if not existing.user and member_data["discord_id"] in users_by_discord_id:
                existing.user = users_by_discord_id[member_data["discord_id"]]
                linked += 1

            existing.save()

            if was_left:
                rejoined += 1
            else:
                updated += 1
        else:
            user = users_by_discord_id.get(member_data["discord_id"])
            GuildMember.objects.create(
                discord_id=member_data["discord_id"],
                username=member_data.get("username", ""),
                display_name=member_data.get("display_name") or "",
                nickname=member_data.get("nickname") or "",
                avatar_hash=member_data.get("avatar_hash") or "",
                roles=member_data.get("roles") or [],
                joined_at=joined_at,
                is_bot=bool(member_data.get("is_bot", False)),
                user=user,
            )
            created += 1
            if user:
                linked += 1

    # Mark members not in payload as left. Iterate so we can generate a ticket
    # for each freshly-departed member; a bulk UPDATE would skip the audit trail
    # admins rely on.
    members_to_mark_left = existing_discord_ids - received_discord_ids
    left = 0
    if members_to_mark_left:
        now = timezone.now()
        departed = list(
            GuildMember.objects.filter(
                discord_id__in=members_to_mark_left,
                date_left__isnull=True,
            )
        )
        for gm in departed:
            gm.date_left = now
            gm.save(update_fields=["date_left", "date_modified"])
            try:
                create_member_left_ticket(gm)
            except Exception as exc:
                logfire.error(
                    "Failed to create member-left ticket",
                    guild_member_id=gm.pk,
                    discord_id=gm.discord_id,
                    error=str(exc),
                )
        left = len(departed)

    total_active = GuildMember.objects.filter(date_left__isnull=True).count()

    logfire.info(
        "Guild members synced",
        source=source,
        created=created,
        updated=updated,
        rejoined=rejoined,
        left=left,
        linked=linked,
        total_received=len(members),
        total_active=total_active,
    )

    return {
        "created": created,
        "updated": updated,
        "rejoined": rejoined,
        "left": left,
        "linked": linked,
        "total_received": len(members),
        "total_active": total_active,
    }
