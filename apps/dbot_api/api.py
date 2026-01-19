"""Discord Bot API endpoints."""

import contextlib
from datetime import datetime, timedelta

from constance import config as constance_config
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from ninja import NinjaAPI, Schema
from ninja.security import APIKeyHeader

from apps.accounts.models import GuildMember, User
from apps.dbot_api.models import BotStats
from apps.magic_links.models import MagicLink
from apps.team.models import DiscordRole, MembershipApplication, RosterFilter
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftpower.tasks import update_team_results, update_team_riders
from apps.zwiftracing.models import ZRRider


def _get_verification_status(user: User) -> dict:
    """Get weight/height/power verification status for a user.

    Args:
        user: The user to check verification for.

    Returns:
        Dict with verification status for each type.

    """
    result = {}
    for verify_type in ["weight_full", "weight_light", "height", "power"]:
        # Get the most recent verified record
        verified_record = user.race_ready_records.filter(
            verify_type=verify_type, status="verified"
        ).first()

        # Check if there's a pending record
        pending_record = user.race_ready_records.filter(
            verify_type=verify_type, status="pending"
        ).first()

        if verified_record:
            verified_date = (
                verified_record.reviewed_date.isoformat() if verified_record.reviewed_date else None
            )
            # If verified record is expired AND there's a pending record, show pending
            if verified_record.is_expired and pending_record:
                result[verify_type] = {
                    "verified": False,
                    "verified_date": verified_date,
                    "days_remaining": verified_record.days_remaining,
                    "is_expired": True,
                    "status": "Pending (expired)",
                    "has_pending": True,
                    "pending_date": pending_record.date_created.isoformat(),
                }
            else:
                # Normal verified record (valid or expired without pending)
                result[verify_type] = {
                    "verified": True,
                    "verified_date": verified_date,
                    "days_remaining": verified_record.days_remaining,
                    "is_expired": verified_record.is_expired,
                    "status": verified_record.validity_status,
                    "has_pending": pending_record is not None,
                }
        elif pending_record:
            # No verified record but has pending
            result[verify_type] = {
                "verified": False,
                "verified_date": None,
                "days_remaining": None,
                "is_expired": False,
                "status": "Pending",
                "has_pending": True,
                "pending_date": pending_record.date_created.isoformat(),
            }
        else:
            # No records at all
            result[verify_type] = {
                "verified": False,
                "verified_date": None,
                "days_remaining": None,
                "is_expired": False,
                "status": "No record",
                "has_pending": False,
            }
    return result


class DiscordRoleSchema(Schema):
    """Schema for a Discord role from the bot."""

    id: str
    name: str
    color: int = 0
    position: int = 0
    managed: bool = False
    mentionable: bool = False


class SyncGuildRolesRequest(Schema):
    """Request schema for syncing all guild roles."""

    roles: list[DiscordRoleSchema]


class SyncUserRolesRequest(Schema):
    """Request schema for syncing a user's roles."""

    role_ids: list[str]


class GuildMemberSchema(Schema):
    """Schema for a Discord guild member from the bot."""

    discord_id: str
    username: str
    display_name: str = ""
    nickname: str = ""
    avatar_hash: str = ""
    roles: list[str] = []  # noqa: RUF012
    joined_at: str | None = None  # ISO format datetime
    is_bot: bool = False


class SyncGuildMembersRequest(Schema):
    """Request schema for syncing all guild members."""

    members: list[GuildMemberSchema]


class CreateRosterFilterRequest(Schema):
    """Request schema for creating a filtered roster link."""

    discord_ids: list[str]
    channel_name: str = ""


class MembershipApplicationCreateSchema(Schema):
    """Schema for creating a membership application."""

    discord_id: str
    discord_username: str
    server_nickname: str = ""
    avatar_url: str = ""
    guild_avatar_url: str = ""
    discord_user_data: dict = {}  # noqa: RUF012
    discord_member_data: dict = {}  # noqa: RUF012
    modal_form_data: dict = {}  # noqa: RUF012
    first_name: str = ""
    last_name: str = ""
    applicant_notes: str = ""


class DBotAuth(APIKeyHeader):
    r"""API key authentication via X-API-Key, X-Guild-Id, and X-Discord-User-Id headers.

    Required headers::

        X-API-Key: <DBOT_AUTH_KEY value>
        X-Guild-Id: <Discord guild/server ID>
        X-Discord-User-Id: <Discord user ID who triggered the command>

    Example:
        curl -H "X-API-Key: your-secret-key" \
             -H "X-Guild-Id: 123456789012345678" \
             -H "X-Discord-User-Id: 987654321098765432" \
             http://localhost:8000/api/dbot/zwiftpower_profile/12345

    """

    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str | None) -> dict | None:
        """Authenticate request using API key, guild ID, and Discord user ID.

        Args:
            request: The HTTP request.
            key: The API key from the header.

        Returns:
            Dict with auth info if valid, None otherwise.

        """
        # Verify API key
        if not constance_config.DBOT_AUTH_KEY or key != constance_config.DBOT_AUTH_KEY:
            return None

        # Verify guild_id header matches configured GUILD_ID
        guild_id = request.headers.get("X-Guild-Id")
        if not guild_id:
            return None

        # Get Discord user ID (required for logging)
        discord_user_id = request.headers.get("X-Discord-User-Id")
        if not discord_user_id:
            return None

        try:
            # Verify guild_id matches the configured guild
            if int(guild_id) != constance_config.GUILD_ID:
                return None

            # Log the API request
            BotStats.objects.create(
                discord_id=discord_user_id,
                discord_guild_id=guild_id,
                api=request.path,
            )

            return {
                "api_key": key,
                "guild_id": guild_id,
                "discord_user_id": discord_user_id,
            }
        except ValueError:
            return None


api = NinjaAPI(auth=DBotAuth(), urls_namespace="dbot_api")


@api.get("/bot_config")
def get_bot_config(request: HttpRequest) -> dict:
    """Get bot configuration settings from constance.

    Returns configuration values the Discord bot needs to operate.
    The bot should fetch this on startup and periodically (every hour).

    Args:
        request: The HTTP request.

    Returns:
        JSON object with bot configuration settings.

    """
    return {
        "upgrade_channel": str(constance_config.UPGRADE_CHANNEL) if constance_config.UPGRADE_CHANNEL else None,
        "new_arrivals_channel_id": (
            str(constance_config.NEW_ARRIVALS_CHANNEL_ID) if constance_config.NEW_ARRIVALS_CHANNEL_ID else None
        ),
        "team_member_role_id": (
            str(constance_config.TEAM_MEMBER_ROLE_ID) if constance_config.TEAM_MEMBER_ROLE_ID else None
        ),
        "race_ready_role_id": (
            str(constance_config.RACE_READY_ROLE_ID) if constance_config.RACE_READY_ROLE_ID else None
        ),
        "new_arrival_message_public": constance_config.NEW_ARRIVAL_MESSAGE_PUBLIC or None,
        "new_arrival_message_private": constance_config.NEW_ARRIVAL_MESSAGE_PRIVATE or None,
        "send_new_arrival_dm": constance_config.SEND_NEW_ARRIVAL_DM,
    }


@api.get("/zwiftpower_profile/{zwid}")
def get_zwiftpower_profile(request: HttpRequest, zwid: int) -> dict:
    """Get ZwiftPower profile for a rider by zwid.

    Args:
        request: The HTTP request.
        zwid: The Zwift ID to look up.

    Returns:
        JSON object with rider data or error message.

    """
    try:
        rider = ZPTeamRiders.objects.get(zwid=zwid)
        return {
            "zwid": rider.zwid,
            "aid": rider.aid,
            "name": rider.name,
            "flag": rider.flag,
            "age": rider.age,
            "div": rider.div,
            "divw": rider.divw,
            "r": rider.r,
            "rank": float(rider.rank) if rider.rank else None,
            "ftp": rider.ftp,
            "weight": float(rider.weight) if rider.weight else None,
            "skill": rider.skill,
            "skill_race": rider.skill_race,
            "skill_seg": rider.skill_seg,
            "skill_power": rider.skill_power,
            "distance": rider.distance,
            "climbed": rider.climbed,
            "energy": rider.energy,
            "time": rider.time,
            "h_1200_watts": rider.h_1200_watts,
            "h_1200_wkg": float(rider.h_1200_wkg) if rider.h_1200_wkg else None,
            "h_15_watts": rider.h_15_watts,
            "h_15_wkg": float(rider.h_15_wkg) if rider.h_15_wkg else None,
            "status": rider.status,
            "reg": rider.reg,
            "email": rider.email,
            "zada": rider.zada,
            "date_created": rider.date_created.isoformat() if rider.date_created else None,
            "date_modified": rider.date_modified.isoformat() if rider.date_modified else None,
            "date_left": rider.date_left.isoformat() if rider.date_left else None,
        }
    except ZPTeamRiders.DoesNotExist:
        return api.create_response(request, {"error": "Rider not found"}, status=404)  # ty:ignore[invalid-return-type]


@api.get("/team_links")
def get_team_links_magic_link(request: HttpRequest) -> dict:
    """Generate a magic link to the team links page for the Discord user.

    Uses the X-Discord-User-Id header to identify the user.

    Args:
        request: The HTTP request.

    Returns:
        JSON object with magic link URL or error message.

    """
    discord_user_id = request.auth["discord_user_id"]  # ty:ignore[unresolved-attribute]

    try:
        user = User.objects.get(discord_id=discord_user_id)

        # Create a magic link pointing to team links page
        redirect_url = reverse("team:links")
        magic_link = MagicLink.create_for_user(user=user, redirect_url=redirect_url)

        # Build absolute URL
        magic_link_url = request.build_absolute_uri(magic_link.get_absolute_url())

        return {
            "magic_link_url": magic_link_url,
            "expires_in_seconds": MagicLink.EXPIRY_SECONDS,
            "redirect_to": redirect_url,
        }
    except User.DoesNotExist:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {
                "error": "User not found",
                "message": "No account found for this Discord user. Please create an account first.",
            },
            status=404,
        )


@api.post("/sync_guild_roles")
def sync_guild_roles(request: HttpRequest, payload: SyncGuildRolesRequest) -> dict:
    """Sync all guild roles from Discord bot.

    The bot should call this endpoint with all roles from the guild.
    Roles not in the payload will be deleted from the database.

    Args:
        request: The HTTP request.
        payload: The request body with list of roles.

    Returns:
        JSON object with sync results.

    """
    received_role_ids = {role.id for role in payload.roles}
    existing_role_ids = set(DiscordRole.objects.values_list("role_id", flat=True))

    created = 0
    updated = 0
    deleted = 0

    # Create or update roles
    for role_data in payload.roles:
        _, was_created = DiscordRole.objects.update_or_create(
            role_id=role_data.id,
            defaults={
                "name": role_data.name,
                "color": role_data.color,
                "position": role_data.position,
                "managed": role_data.managed,
                "mentionable": role_data.mentionable,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    # Delete roles that no longer exist in Discord
    roles_to_delete = existing_role_ids - received_role_ids
    if roles_to_delete:
        deleted, _ = DiscordRole.objects.filter(role_id__in=roles_to_delete).delete()

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "total": len(payload.roles),
    }


@api.post("/sync_user_roles/{discord_id}")
def sync_user_roles(request: HttpRequest, discord_id: str, payload: SyncUserRolesRequest) -> dict:
    """Sync a user's Discord roles.

    Updates the user's discord_roles field with {role_id: role_name} mapping.

    Args:
        request: The HTTP request.
        discord_id: The Discord user ID.
        payload: The request body with list of role IDs.

    Returns:
        JSON object with sync results or error.

    """
    try:
        user = User.objects.get(discord_id=discord_id)
    except User.DoesNotExist:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {"error": "User not found", "discord_id": discord_id},
            status=404,
        )

    # Build role mapping from DiscordRole table
    role_ids = payload.role_ids
    roles = DiscordRole.objects.filter(role_id__in=role_ids)
    role_map = {role.role_id: role.name for role in roles}

    # Include any role IDs not in our database (with placeholder name)
    for role_id in role_ids:
        if role_id not in role_map:
            role_map[role_id] = f"Unknown Role ({role_id})"

    user.discord_roles = role_map
    user.save(update_fields=["discord_roles"])

    return {
        "discord_id": discord_id,
        "roles_synced": len(role_map),
        "roles": role_map,
        "is_race_ready": user.is_race_ready,
        "race_ready_role_id": str(constance_config.RACE_READY_ROLE_ID) if constance_config.RACE_READY_ROLE_ID else None,
    }


@api.post("/sync_guild_members")
def sync_guild_members(request: HttpRequest, payload: SyncGuildMembersRequest) -> dict:
    """Sync all guild members from Discord bot.

    The bot should call this endpoint with all members from the guild.
    Members not in the payload will be marked as left (date_left set).
    Members are linked to User accounts by matching discord_id.

    NOTE: This only affects GuildMember records and links to User accounts
    that have a discord_id. Regular Django accounts (staff, admin) without
    Discord OAuth are NOT modified or disabled by this sync.

    Args:
        request: The HTTP request.
        payload: The request body with list of guild members.

    Returns:
        JSON object with sync results.

    """
    received_discord_ids = {member.discord_id for member in payload.members}
    existing_discord_ids = set(
        GuildMember.objects.filter(date_left__isnull=True).values_list("discord_id", flat=True)
    )

    created = 0
    updated = 0
    rejoined = 0
    left = 0
    linked = 0

    # Build a lookup of discord_id -> User for linking
    users_by_discord_id = {u.discord_id: u for u in User.objects.filter(discord_id__in=received_discord_ids)}

    # Create or update members
    for member_data in payload.members:
        # Parse joined_at if provided
        joined_at = None
        if member_data.joined_at:
            with contextlib.suppress(ValueError):
                joined_at = datetime.fromisoformat(member_data.joined_at.replace("Z", "+00:00"))

        # Try to find existing member (including those who left)
        existing = GuildMember.objects.filter(discord_id=member_data.discord_id).first()

        if existing:
            # Update existing member
            was_left = existing.date_left is not None
            existing.username = member_data.username
            existing.display_name = member_data.display_name or ""
            existing.nickname = member_data.nickname or ""
            existing.avatar_hash = member_data.avatar_hash or ""
            existing.roles = member_data.roles
            existing.joined_at = joined_at
            existing.is_bot = member_data.is_bot
            existing.date_left = None  # Clear date_left if they're back

            # Link to user if not already linked
            if not existing.user and member_data.discord_id in users_by_discord_id:
                existing.user = users_by_discord_id[member_data.discord_id]
                linked += 1

            existing.save()

            if was_left:
                rejoined += 1
            else:
                updated += 1
        else:
            # Create new member
            user = users_by_discord_id.get(member_data.discord_id)
            GuildMember.objects.create(
                discord_id=member_data.discord_id,
                username=member_data.username,
                display_name=member_data.display_name or "",
                nickname=member_data.nickname or "",
                avatar_hash=member_data.avatar_hash or "",
                roles=member_data.roles,
                joined_at=joined_at,
                is_bot=member_data.is_bot,
                user=user,
            )
            created += 1
            if user:
                linked += 1

    # Mark members not in payload as left
    members_to_mark_left = existing_discord_ids - received_discord_ids
    if members_to_mark_left:
        left = GuildMember.objects.filter(
            discord_id__in=members_to_mark_left,
            date_left__isnull=True,
        ).update(date_left=timezone.now())

    return {
        "created": created,
        "updated": updated,
        "rejoined": rejoined,
        "left": left,
        "linked": linked,
        "total_received": len(payload.members),
        "total_active": GuildMember.objects.filter(date_left__isnull=True).count(),
    }


@api.post("/update_zp_team")
def trigger_update_zp_team(request: HttpRequest) -> dict:
    """Trigger the update_team_riders background task.

    Enqueues the task to fetch team riders from ZwiftPower and update the database.

    Args:
        request: The HTTP request.

    Returns:
        JSON object confirming the task was enqueued.

    """
    update_team_riders.enqueue()
    return {
        "status": "queued",
        "message": "ZwiftPower team update task has been queued.",
    }


@api.post("/update_zp_results")
def trigger_update_zp_results(request: HttpRequest) -> dict:
    """Trigger the update_team_results background task.

    Enqueues the task to fetch team results from ZwiftPower and update the database.

    Args:
        request: The HTTP request.

    Returns:
        JSON object confirming the task was enqueued.

    """
    update_team_results.enqueue()
    return {
        "status": "queued",
        "message": "ZwiftPower team results update task has been queued.",
    }


@api.get("/my_profile")
def get_my_profile(request: HttpRequest) -> dict:
    """Get combined ZwiftPower and Zwift Racing profile for the requesting Discord user.

    Uses X-Discord-User-Id header to identify the user, looks up their linked Zwift ID,
    and returns combined profile data from ZPTeamRiders and ZRRider models.

    Args:
        request: The HTTP request.

    Returns:
        JSON object with combined profile data or error message.

    """
    discord_user_id = request.auth["discord_user_id"]  # ty:ignore[unresolved-attribute]

    # Look up user by Discord ID
    try:
        user = User.objects.get(discord_id=discord_user_id)
    except User.DoesNotExist:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {
                "error": "User not found",
                "message": "No account found for this Discord user. Please create an account first.",
            },
            status=404,
        )

    # Check if user has linked Zwift ID
    if not user.zwid:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {
                "error": "Zwift ID not linked",
                "message": "Your account does not have a linked Zwift ID. Please link your Zwift account first.",
            },
            status=404,
        )

    zwid = user.zwid

    # Build profile response
    profile: dict = {
        "zwid": zwid,
        "discord_username": user.discord_username,
        "zwid_verified": user.zwid_verified,
        "verification": _get_verification_status(user),
        "is_race_ready": user.is_race_ready,
        "race_ready_role_id": str(constance_config.RACE_READY_ROLE_ID) if constance_config.RACE_READY_ROLE_ID else None,
        "zwiftpower": None,
        "zwiftracing": None,
    }

    # Get ZwiftPower data
    try:
        zp_rider = ZPTeamRiders.objects.get(zwid=zwid)
        profile["zwiftpower"] = {
            "name": zp_rider.name,
            "flag": zp_rider.flag,
            "age": zp_rider.age,
            "div": zp_rider.div,
            "divw": zp_rider.divw,
            "r": zp_rider.r,
            "rank": float(zp_rider.rank) if zp_rider.rank else None,
            "ftp": zp_rider.ftp,
            "weight": float(zp_rider.weight) if zp_rider.weight else None,
            "skill": zp_rider.skill,
            "skill_race": zp_rider.skill_race,
            "skill_seg": zp_rider.skill_seg,
            "skill_power": zp_rider.skill_power,
            "h_15_watts": zp_rider.h_15_watts,
            "h_15_wkg": float(zp_rider.h_15_wkg) if zp_rider.h_15_wkg else None,
            "h_1200_watts": zp_rider.h_1200_watts,
            "h_1200_wkg": float(zp_rider.h_1200_wkg) if zp_rider.h_1200_wkg else None,
            "distance_km": round(zp_rider.distance / 1000, 1) if zp_rider.distance else 0,
            "climbed_m": zp_rider.climbed,
            "time_hours": round(zp_rider.time / 3600, 1) if zp_rider.time else 0,
        }
    except ZPTeamRiders.DoesNotExist:
        pass  # zwiftpower remains None

    # Get Zwift Racing data
    try:
        zr_rider = ZRRider.objects.get(zwid=zwid)
        profile["zwiftracing"] = {
            "name": zr_rider.name,
            "country": zr_rider.country,
            "gender": zr_rider.gender,
            "height": zr_rider.height,
            "weight": float(zr_rider.weight) if zr_rider.weight else None,
            "zp_category": zr_rider.zp_category,
            "zp_ftp": zr_rider.zp_ftp,
            # Critical Power
            "power_cp": float(zr_rider.power_cp) if zr_rider.power_cp else None,
            # Race ratings
            "race_current_rating": float(zr_rider.race_current_rating) if zr_rider.race_current_rating else None,
            "race_current_category": zr_rider.race_current_category,
            "race_max30_rating": float(zr_rider.race_max30_rating) if zr_rider.race_max30_rating else None,
            "race_max30_category": zr_rider.race_max30_category,
            "race_max90_rating": float(zr_rider.race_max90_rating) if zr_rider.race_max90_rating else None,
            "race_max90_category": zr_rider.race_max90_category,
            # Race stats
            "race_finishes": zr_rider.race_finishes,
            "race_wins": zr_rider.race_wins,
            "race_podiums": zr_rider.race_podiums,
            "race_dnfs": zr_rider.race_dnfs,
            # Phenotype
            "phenotype_value": zr_rider.phenotype_value,
            "phenotype_sprinter": float(zr_rider.phenotype_sprinter) if zr_rider.phenotype_sprinter else None,
            "phenotype_puncheur": float(zr_rider.phenotype_puncheur) if zr_rider.phenotype_puncheur else None,
            "phenotype_pursuiter": float(zr_rider.phenotype_pursuiter) if zr_rider.phenotype_pursuiter else None,
            "phenotype_climber": float(zr_rider.phenotype_climber) if zr_rider.phenotype_climber else None,
            "phenotype_tt": float(zr_rider.phenotype_tt) if zr_rider.phenotype_tt else None,
            # Power curve (w/kg)
            "power_wkg5": float(zr_rider.power_wkg5) if zr_rider.power_wkg5 else None,
            "power_wkg15": float(zr_rider.power_wkg15) if zr_rider.power_wkg15 else None,
            "power_wkg60": float(zr_rider.power_wkg60) if zr_rider.power_wkg60 else None,
            "power_wkg300": float(zr_rider.power_wkg300) if zr_rider.power_wkg300 else None,
            "power_wkg1200": float(zr_rider.power_wkg1200) if zr_rider.power_wkg1200 else None,
        }
    except ZRRider.DoesNotExist:
        pass  # zwiftracing remains None

    # Check if we found any data
    if profile["zwiftpower"] is None and profile["zwiftracing"] is None:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {
                "error": "No profile data found",
                "message": f"No ZwiftPower or Zwift Racing data found for Zwift ID {zwid}.",
                "zwid": zwid,
            },
            status=404,
        )

    return profile


@api.get("/search_teammates")
def search_teammates(request: HttpRequest, q: str = "") -> dict:
    """Search for teammates by name for autocomplete.

    Args:
        request: The HTTP request.
        q: Search query string (partial name match).

    Returns:
        JSON object with list of matching teammates (max 25 for Discord autocomplete limit).

    """
    if not q or len(q) < 2:
        return {"results": []}

    # Search ZPTeamRiders by name (case-insensitive contains)
    # Only include active team members (date_left is null)
    matches = (
        ZPTeamRiders.objects
        .filter(name__icontains=q, date_left__isnull=True)
        .values("zwid", "name", "flag")
        .order_by("name")[:25]
    )

    results = [{"zwid": m["zwid"], "name": m["name"], "flag": m["flag"]} for m in matches]

    return {"results": results}


@api.get("/teammate_profile/{zwid}")
def get_teammate_profile(request: HttpRequest, zwid: int) -> dict:
    """Get combined ZwiftPower and Zwift Racing profile for a teammate by Zwift ID.

    Args:
        request: The HTTP request.
        zwid: The Zwift ID to look up.

    Returns:
        JSON object with combined profile data or error message.

    """
    # Build profile response
    profile: dict = {
        "zwid": zwid,
        "account": None,
        "verification": None,
        "zwiftpower": None,
        "zwiftracing": None,
    }

    # Check if user has a linked account
    try:
        user = User.objects.get(zwid=zwid)
        profile["account"] = {
            "discord_username": user.discord_username,
            "discord_nickname": user.discord_nickname,
            "zwid_verified": user.zwid_verified,
        }
        profile["verification"] = _get_verification_status(user)
    except User.DoesNotExist:
        pass  # account and verification remain None

    # Get ZwiftPower data
    try:
        zp_rider = ZPTeamRiders.objects.get(zwid=zwid)
        profile["zwiftpower"] = {
            "name": zp_rider.name,
            "flag": zp_rider.flag,
            "age": zp_rider.age,
            "div": zp_rider.div,
            "divw": zp_rider.divw,
            "r": zp_rider.r,
            "rank": float(zp_rider.rank) if zp_rider.rank else None,
            "ftp": zp_rider.ftp,
            "weight": float(zp_rider.weight) if zp_rider.weight else None,
            "skill": zp_rider.skill,
            "skill_race": zp_rider.skill_race,
            "skill_seg": zp_rider.skill_seg,
            "skill_power": zp_rider.skill_power,
            "h_15_watts": zp_rider.h_15_watts,
            "h_15_wkg": float(zp_rider.h_15_wkg) if zp_rider.h_15_wkg else None,
            "h_1200_watts": zp_rider.h_1200_watts,
            "h_1200_wkg": float(zp_rider.h_1200_wkg) if zp_rider.h_1200_wkg else None,
            "distance_km": round(zp_rider.distance / 1000, 1) if zp_rider.distance else 0,
            "climbed_m": zp_rider.climbed,
            "time_hours": round(zp_rider.time / 3600, 1) if zp_rider.time else 0,
        }
    except ZPTeamRiders.DoesNotExist:
        pass  # zwiftpower remains None

    # Get Zwift Racing data
    try:
        zr_rider = ZRRider.objects.get(zwid=zwid)
        profile["zwiftracing"] = {
            "name": zr_rider.name,
            "country": zr_rider.country,
            "gender": zr_rider.gender,
            "height": zr_rider.height,
            "weight": float(zr_rider.weight) if zr_rider.weight else None,
            "zp_category": zr_rider.zp_category,
            "zp_ftp": zr_rider.zp_ftp,
            # Critical Power
            "power_cp": float(zr_rider.power_cp) if zr_rider.power_cp else None,
            # Race ratings
            "race_current_rating": float(zr_rider.race_current_rating) if zr_rider.race_current_rating else None,
            "race_current_category": zr_rider.race_current_category,
            "race_max30_rating": float(zr_rider.race_max30_rating) if zr_rider.race_max30_rating else None,
            "race_max30_category": zr_rider.race_max30_category,
            "race_max90_rating": float(zr_rider.race_max90_rating) if zr_rider.race_max90_rating else None,
            "race_max90_category": zr_rider.race_max90_category,
            # Race stats
            "race_finishes": zr_rider.race_finishes,
            "race_wins": zr_rider.race_wins,
            "race_podiums": zr_rider.race_podiums,
            "race_dnfs": zr_rider.race_dnfs,
            # Phenotype
            "phenotype_value": zr_rider.phenotype_value,
            "phenotype_sprinter": float(zr_rider.phenotype_sprinter) if zr_rider.phenotype_sprinter else None,
            "phenotype_puncheur": float(zr_rider.phenotype_puncheur) if zr_rider.phenotype_puncheur else None,
            "phenotype_pursuiter": float(zr_rider.phenotype_pursuiter) if zr_rider.phenotype_pursuiter else None,
            "phenotype_climber": float(zr_rider.phenotype_climber) if zr_rider.phenotype_climber else None,
            "phenotype_tt": float(zr_rider.phenotype_tt) if zr_rider.phenotype_tt else None,
            # Power curve (w/kg)
            "power_wkg5": float(zr_rider.power_wkg5) if zr_rider.power_wkg5 else None,
            "power_wkg15": float(zr_rider.power_wkg15) if zr_rider.power_wkg15 else None,
            "power_wkg60": float(zr_rider.power_wkg60) if zr_rider.power_wkg60 else None,
            "power_wkg300": float(zr_rider.power_wkg300) if zr_rider.power_wkg300 else None,
            "power_wkg1200": float(zr_rider.power_wkg1200) if zr_rider.power_wkg1200 else None,
        }
    except ZRRider.DoesNotExist:
        pass  # zwiftracing remains None

    # Check if we found any data
    if profile["zwiftpower"] is None and profile["zwiftracing"] is None:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {
                "error": "Teammate not found",
                "message": f"No profile data found for Zwift ID {zwid}.",
                "zwid": zwid,
            },
            status=404,
        )

    return profile


@api.post("/roster_filter")
def create_roster_filter(request: HttpRequest, payload: CreateRosterFilterRequest) -> dict:
    """Create a filtered roster link from a list of Discord IDs.

    Creates a temporary RosterFilter record with 5-minute expiration.
    Used by the Discord bot /in_channel command.

    Args:
        request: The HTTP request.
        payload: The request body with list of Discord IDs and channel name.

    Returns:
        JSON object with filter ID, URL, and expiration info.

    """
    discord_user_id = request.auth["discord_user_id"]  # ty:ignore[unresolved-attribute]

    # Create filter with 5-minute expiration
    expires_at = timezone.now() + timedelta(minutes=5)

    roster_filter = RosterFilter.objects.create(
        discord_ids=payload.discord_ids,
        channel_name=payload.channel_name,
        created_by_discord_id=discord_user_id,
        expires_at=expires_at,
    )

    # Build absolute URL for the filtered roster
    filter_url = request.build_absolute_uri(
        reverse("team:filtered_roster", kwargs={"filter_id": roster_filter.id})
    )

    return {
        "filter_id": str(roster_filter.id),
        "url": filter_url,
        "expires_in_seconds": 300,
        "member_count": len(payload.discord_ids),
        "channel_name": payload.channel_name,
    }


@api.post("/membership_application")
def create_membership_application(request: HttpRequest, payload: MembershipApplicationCreateSchema) -> dict:
    """Create a new membership application from Discord modal.

    Called by the Discord bot when a user submits the join_the_coalition modal.
    If an application already exists for the Discord ID, returns the existing one.

    Args:
        request: The HTTP request.
        payload: The application data from the Discord modal.

    Returns:
        JSON object with application ID, URL, and status.

    """
    # Check if application already exists for this discord_id
    existing = MembershipApplication.objects.filter(discord_id=payload.discord_id).first()
    if existing:
        # Build absolute URL for the application
        application_url = request.build_absolute_uri(
            reverse("team:application_public", kwargs={"pk": existing.id})
        )
        return {
            "id": str(existing.id),
            "discord_id": existing.discord_id,
            "discord_username": existing.discord_username,
            "status": existing.status,
            "application_url": application_url,
            "is_complete": existing.is_complete,
            "date_created": existing.date_created.isoformat(),
            "already_exists": True,
        }

    # Create new application
    application = MembershipApplication.objects.create(
        discord_id=payload.discord_id,
        discord_username=payload.discord_username,
        server_nickname=payload.server_nickname,
        avatar_url=payload.avatar_url,
        guild_avatar_url=payload.guild_avatar_url,
        discord_user_data=payload.discord_user_data,
        discord_member_data=payload.discord_member_data,
        modal_form_data=payload.modal_form_data,
        first_name=payload.first_name,
        last_name=payload.last_name,
        applicant_notes=payload.applicant_notes,
    )

    # Build absolute URL for the application
    application_url = request.build_absolute_uri(
        reverse("team:application_public", kwargs={"pk": application.id})
    )

    return {
        "id": str(application.id),
        "discord_id": application.discord_id,
        "discord_username": application.discord_username,
        "status": application.status,
        "application_url": application_url,
        "is_complete": application.is_complete,
        "date_created": application.date_created.isoformat(),
        "already_exists": False,
    }


@api.get("/membership_application/{discord_id}")
def get_membership_application(request: HttpRequest, discord_id: str) -> dict:
    """Get membership application by Discord ID.

    Args:
        request: The HTTP request.
        discord_id: The Discord user ID to look up.

    Returns:
        JSON object with application data or error if not found.

    """
    try:
        application = MembershipApplication.objects.get(discord_id=discord_id)

        # Build absolute URL for the application
        application_url = request.build_absolute_uri(
            reverse("team:application_public", kwargs={"pk": application.id})
        )

        return {
            "id": str(application.id),
            "discord_id": application.discord_id,
            "discord_username": application.discord_username,
            "server_nickname": application.server_nickname,
            "first_name": application.first_name,
            "last_name": application.last_name,
            "status": application.status,
            "status_display": application.get_status_display(),
            "application_url": application_url,
            "is_complete": application.is_complete,
            "is_editable": application.is_editable,
            "agree_privacy": application.agree_privacy,
            "agree_tos": application.agree_tos,
            "date_created": application.date_created.isoformat(),
            "date_modified": application.date_modified.isoformat(),
        }
    except MembershipApplication.DoesNotExist:
        return api.create_response(  # ty:ignore[invalid-return-type]
            request,
            {"error": "Application not found", "discord_id": discord_id},
            status=404,
        )
