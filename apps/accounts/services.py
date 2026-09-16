"""Service functions for accounts app."""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import logfire
from django.db.models import BooleanField, ExpressionWrapper, F, Q
from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import QuerySet

    from apps.accounts.models import GuildMember, User
    from apps.team.models import MembershipApplication

DISCORD_API_BASE = "https://discord.com/api/v10"
GUILD_MEMBER_PAGE_SIZE = 1000

# Access hangs on GuildMember.date_left (apps.accounts.membership), so a member list that comes
# back short must not be read as a mass departure: every rider it leaves out would be signed out
# and lose their API keys until the next good sync. Past this many departures in one run, and
# this share of the active rows, none are stamped until an admin confirms them with the
# ``allow_mass_departure`` option on the sync_guild_members task. A normal run sees a handful.
# The same limit, measured against the Discord-linked accounts, holds back the sweep that
# records accounts no sync has ever listed (_record_unseen_accounts).
MASS_DEPARTURE_FLOOR = 10
MASS_DEPARTURE_SHARE = 0.05

# A Discord user id (snowflake) as the bot reports it: ASCII digits only, and no longer than
# GuildMember.discord_id holds (a 64-bit snowflake has at most 20 digits).
DISCORD_SNOWFLAKE_RE = re.compile(r"[0-9]{1,20}")


class GuildSyncRefusedError(RuntimeError):
    """A guild member sync saved the members it received but refused to sign anybody out.

    Raised by the ``sync_guild_members`` task once ``apply_guild_member_sync`` has returned,
    so the upserts are already committed. It exists to make the task record read "Failed":
    a returned dict reads as a success on the Run Now page, and a refusal repeats on every
    run until an admin acts on it.
    """


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
#
# The Zwift ID is deliberately absent. A registration's zwid is only worth anything as part
# of its zauth link, so the link itself is moved (carry_over_zwift_link) and the member's
# zwid then comes from the service. Copying the number across would either put an
# unconfirmed claim on the account or stamp a verification this platform never made.
FIELD_MAPPING: list[tuple[str, str, str, Callable | None]] = [
    ("first_name", "first_name", "First Name", None),
    ("last_name", "last_name", "Last Name", None),
    ("email", "email", "Email", None),
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

# The get_importable_fields key for the Zwift carry-over row. Not a field on either model.
ZWIFT_LINK_KEY = "zwift_link"


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


def _user_has_value(user: User, user_field: str) -> bool:
    """Whether the member already has an answer that the import must not overwrite.

    ``False`` is an answer ("No"): the only boolean imported, ``dual_recording``, is
    nullable, so ``None`` is what unanswered looks like. Counting ``False`` as blank kept
    re-offering -- and re-importing -- a "No" the member had already given, so the banner
    never went away. A blank ``Country`` compares equal to ``""``, so it counts as
    unanswered.

    Args:
        user: The member.
        user_field: The User field to check.

    Returns:
        True when the field holds a value.

    """
    value = getattr(user, user_field, None)
    return value is not None and value != ""


# How long "this registration holds no Zwift link" is remembered. An approved registration
# cannot connect (the connect view refuses once it is locked), so the answer does not go
# stale in practice; the cache only spares the profile page a service call on every load for
# members whose registration was verified by a retired path. A "yes" is never cached: the
# import moves the link away, and the next check has to see that.
REGISTRATION_NO_LINK_CACHE_SECONDS = 3600


def _registration_link_cache_key(application: MembershipApplication) -> str:
    """Build the cache key for a registration's "no Zwift link" answer.

    Args:
        application: The registration.

    Returns:
        The cache key.

    """
    return f"registration-zwift-link:v1:{application.pk}"


def registration_zwift_link(application: MembershipApplication) -> str | None:
    """Ask the service whether the registration holds a Zwift link, and for which zwid.

    ``zwift_verified`` alone does not say so: registrations verified by the retired Sauce
    password flow or by the retired staff grant carry the flag with no link behind it.

    Args:
        application: The registration.

    Returns:
        The linked zwid, ``""`` when linked but the service reported no zwid, or None when
        there is no link -- or when the service could not say (unconfigured, unreachable),
        since nothing can be moved then either.

    """
    from django.core.cache import cache

    from apps.zwift import client as zwift_client

    key = _registration_link_cache_key(application)
    if cache.get(key) is False:
        return None
    status = zwift_client.get_connection_status(str(application.pk))
    if status is None:
        return None
    if not status.get("connected"):
        cache.set(key, False, REGISTRATION_NO_LINK_CACHE_SECONDS)
        return None
    return str(status.get("zwid") or "")


def _logged_in_as_registrant(user: User, application: MembershipApplication) -> bool:
    """Whether the member's account holds the Discord login that made the registration.

    ``User.discord_id`` alone is not enough: it is editable in the Django admin (it is how an
    account is moved to a rider's new Discord login), so a staff user could point their own
    account at a registrant and collect that registrant's Zwift verification. The Discord
    ``SocialAccount`` is written by the OAuth login and is read-only in the Django admin
    (``SocialAccountAdmin`` in ``apps/accounts/admin.py``), so it is much harder to repoint.

    What this does not prove: that the login in use right now is that Discord account (a
    ``SocialAccount`` outlives the session that wrote it), or anything against somebody with
    shell or database access, who can write the row directly.

    Args:
        user: The member.
        application: The registration.

    Returns:
        True when the member's Discord social account has the registration's Discord ID.

    """
    from allauth.socialaccount.models import SocialAccount

    if not application.discord_id:
        return False
    return SocialAccount.objects.filter(user=user, provider="discord", uid=application.discord_id).exists()


def _carry_over_allowed(user: User, application: MembershipApplication) -> bool:
    """Run the carry-over checks that need no call to the Zwift service.

    Args:
        user: The member.
        application: Their approved registration.

    Returns:
        True when the registration is verified, the member is not zauth-verified, and the
        member's account holds the registration's Discord login.

    """
    # A member who is already zauth-verified has their own link, and theirs always wins.
    # Tying the offer to that is also what lets the import banner go away once they are.
    if not application.zwift_verified or user.is_zauth_verified:
        return False
    return _logged_in_as_registrant(user, application)


def _carry_over_zwid(user: User, application: MembershipApplication) -> str | None:
    """Return the registration link's zwid when the import should move that link.

    Args:
        user: The member.
        application: Their approved registration.

    Returns:
        The zwid (``""`` if the service reported none) when the carry-over is on offer,
        otherwise None.

    """
    if not _carry_over_allowed(user, application):
        return None
    return registration_zwift_link(application)


def can_carry_over_zwift(user: User, application: MembershipApplication) -> bool:
    """Whether the registration holds a Zwift link worth moving to this member.

    Offered only when the registration is verified, the service confirms it holds a link,
    the member is not already zauth-verified, and the member's account holds the Discord
    login that made the registration (see :func:`_logged_in_as_registrant` for what that
    does and does not prove).

    Args:
        user: The member.
        application: Their approved registration.

    Returns:
        True when the import should try to move the link.

    """
    return _carry_over_zwid(user, application) is not None


def get_importable_fields(application: MembershipApplication, user: User) -> dict[str, dict]:
    """Return what importing this registration would change on the member's profile.

    Only fields the member has left blank are listed, because the import never overwrites.
    The list therefore empties once everything has been imported, and the banner offering
    the import goes with it.

    Args:
        application: The MembershipApplication to extract fields from.
        user: The member the import would write to.

    Returns:
        Dictionary mapping field names to {label, value, display_value, user_field}. The
        Zwift carry-over, when offered, is the :data:`ZWIFT_LINK_KEY` entry; its
        ``user_field`` is empty because it moves a link rather than copying a value.

    """
    result = {}

    for app_field, user_field, label, transform in FIELD_MAPPING:
        value = getattr(application, app_field, None)

        # Skip empty values
        if value is None or value == "":
            continue
        if _user_has_value(user, user_field):
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

    link_zwid = _carry_over_zwid(user, application)
    if link_zwid is not None:
        result[ZWIFT_LINK_KEY] = {
            "label": "Zwift Account",
            "value": link_zwid,
            "display_value": f"Connected, Zwift ID {link_zwid}" if link_zwid else "Connected",
            "user_field": "",
        }

    return result


def import_application_to_user(user: User, application: MembershipApplication) -> list[str]:
    """Copy fields from application to user.

    Never touches the Zwift fields; :func:`carry_over_zwift_link` deals with those.

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

        # Skip if user already has a value (don't overwrite)
        if _user_has_value(user, user_field):
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


@dataclass(frozen=True)
class ZwiftCarryOver:
    """What :func:`carry_over_zwift_link` did.

    Attributes:
        outcome: ``"skipped"`` when there was nothing to move, otherwise the
            :class:`apps.zwift.client.RelinkOutcome` value (``moved``, ``not_found``,
            ``conflict``, ``error`` or ``unconfigured``).
        verified: Whether the member is zauth-verified afterwards.
        zwid: The member's zwid afterwards, when they are zauth-verified.

    """

    outcome: str
    verified: bool = False
    zwid: int | None = None


def carry_over_zwift_link(
    user: User, application: MembershipApplication, *, offered: bool | None = None
) -> ZwiftCarryOver:
    """Move the registration's Zwift link onto the member's account and verify them by it.

    The service holds the link under the registration's UUID, because the registration came
    before the account. This asks the service to re-key it to the member, then reads the
    member's status back and applies it exactly as the ``/user/zauth/`` page does, so the
    member is verified with the service's zwid straight away rather than at the next hourly
    reconcile.

    On a conflict the member's own link wins: the registration's link can never be used, so
    it is dropped, and the member's own status is applied instead.

    After an error or a "no link to move", the member's status is read back too. Both can
    follow a move that did happen -- a response lost after the service committed it, or a
    second submit finding the link already moved by the first -- and the read is what tells
    the two apart. It never revokes on an unanswered read (``apply_status`` skips None).

    Args:
        user: The member.
        application: Their approved registration.
        offered: Whether the service confirmed, earlier in this request, that the registration
            holds a link (``ZWIFT_LINK_KEY in get_importable_fields(...)``). Passing it saves a
            second service call; the checks that need no call are made again regardless. When
            None, the service is asked.

    Returns:
        A :class:`ZwiftCarryOver`.

    """
    from apps.zwift import client as zwift_client
    from apps.zwift import verification

    if offered is None:
        offered = can_carry_over_zwift(user, application)
    else:
        offered = offered and _carry_over_allowed(user, application)
    if not offered:
        return ZwiftCarryOver("skipped", verified=user.is_zauth_verified)

    if user.is_staff:
        # The ownership check rests on a SocialAccount row, which staff with database or shell
        # access can still write. A staff account collecting a registration's Zwift link is
        # rare enough to be worth a look every time.
        logfire.warning(
            "Registration Zwift link carry-over for a staff account",
            user_id=user.pk,
            discord_id=user.discord_id,
            application_id=str(application.pk),
        )

    result = zwift_client.relink_connection(str(application.pk), str(user.pk))
    applied = ""
    link_removed = None

    if result.outcome in (zwift_client.RelinkOutcome.MOVED, zwift_client.RelinkOutcome.NOT_FOUND):
        # The service just answered, and the member's link may be new, so a read that failed a
        # moment ago must not stand in for the answer.
        zwift_client.forget_status_failure(str(user.pk))

    if result.outcome == zwift_client.RelinkOutcome.MOVED:
        status = zwift_client.get_connection_status(str(user.pk))
        if status is None and result.zwid:
            # The relink response is the service's own statement that the link is now this
            # member's, so it stands in when the status read straight after it fails.
            status = {"connected": True, "zwid": result.zwid}
        applied = verification.apply_status(user, status)
    elif result.outcome == zwift_client.RelinkOutcome.CONFLICT:
        link_removed = zwift_client.disconnect(str(application.pk))
        applied = verification.sync_user_verification(user)
    elif result.outcome in (zwift_client.RelinkOutcome.ERROR, zwift_client.RelinkOutcome.NOT_FOUND):
        applied = verification.sync_user_verification(user)

    verified = user.is_zauth_verified
    logfire.info(
        "Registration Zwift link carry-over",
        user_id=user.pk,
        discord_id=user.discord_id,
        application_id=str(application.pk),
        outcome=str(result.outcome),
        verification_outcome=applied,
        zwift_link_removed=link_removed,
        verified=verified,
        zwid=user.zwid if verified else None,
    )
    return ZwiftCarryOver(str(result.outcome), verified=verified, zwid=user.zwid if verified else None)


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

    Raises:
        ValueError: If a full page carries no member id to page on from, so the
            list would be incomplete.

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
            after = str((page[-1].get("user") or {}).get("id", ""))
            if not after:
                # A full page with no cursor means members are missing, and a short list now
                # signs riders out (see apply_guild_member_sync). Fail the run instead.
                logfire.error("Discord guild member page had no cursor", fetched_so_far=len(members))
                msg = "Discord returned a full guild member page without a user id to page from"
                raise ValueError(msg)

    return members


def _release_user_link(user, *, keep_pk: int | None = None) -> int:
    """Detach a user from any other GuildMember row before linking them to this one.

    ``GuildMember.user`` is a OneToOneField, so a user can hold exactly one row. When
    somebody loses a Discord account and makes a new one, their ``User.discord_id`` moves
    to the new account while the old ``GuildMember`` keeps the link -- and the next sync
    tries to create a second row for the same user and dies on
    ``accounts_guildmember_user_id_key``, taking the whole sweep with it.

    The old row is kept, not deleted: it is the record that that Discord account was in
    the guild and when it left. Only the user link is released.

    Args:
        user: The user about to be linked, or None.
        keep_pk: A GuildMember pk to leave alone (the row doing the claiming).

    Returns:
        The number of rows unlinked.

    """
    from apps.accounts.models import GuildMember

    if user is None:
        return 0
    stale = GuildMember.objects.filter(user=user)
    if keep_pk is not None:
        stale = stale.exclude(pk=keep_pk)
    rows = list(stale.values_list("pk", "discord_id"))
    if not rows:
        return 0
    stale.update(user=None)
    logfire.info(
        "Released a stale GuildMember user link",
        user_id=user.pk,
        released=[{"guild_member_id": pk, "discord_id": did} for pk, did in rows],
    )
    return len(rows)


def delete_user_account(user, *, deleted_by=None) -> dict:
    """Delete a user account and everything that goes with it.

    Shared by the rider's own "Delete Account" page and the admin Compliance tool, so the
    two cannot drift: an erasure carried out on somebody's behalf has to do exactly what
    they would have got themselves.

    Order matters. Verification photos are purged first, because RaceReadyRecord rows are
    removed by Django's Collector -- which bulk-deletes and never calls ``Model.delete()``
    -- and once the rows are gone the files are unreachable except by enumerating the
    storage prefix. The upstream Zwift link is dropped next, since it lives in another
    service and nothing here can reach it afterwards.

    Neither of those is allowed to block the deletion. Refusing to erase somebody because
    one blob is unreadable, or because an unrelated service is down, is the worse outcome;
    the failures are recorded instead.

    Args:
        user: The account to delete.
        deleted_by: The user carrying this out, when it is not the account holder.

    Returns:
        The audit record, already logged. Name and email are deliberately absent: the
        person is being forgotten, and Logfire retention is not ours to control.
        ``discord_id`` and ``zwid`` are kept because the records that outlive the account
        (GuildMember, MembershipApplication, and the zwid-keyed ZwiftPower / Zwift Racing
        tables) are keyed by them, so a later erasure request is unanswerable without them.

        ``complete`` says whether every step actually finished, and ``incomplete_reasons``
        lists what did not in words a caller can show someone. Check ``complete`` rather than
        assuming success -- the deletion proceeds even when a step fails, by design.

    """
    from django.db.models import Q

    from apps.accounts.models import GuildMember
    from apps.team.models import MembershipApplication
    from apps.team.services import purge_user_verification_media, release_application_zwift_links
    from apps.zwift import client as zwift_client

    media = purge_user_verification_media(user)

    # The zauth service holds its own record of the zwid-to-user link. Nothing in this
    # database can reach it once the row is gone.
    zauth_disconnected = None
    zauth_error = None
    try:
        link = zwift_client.disconnect_link(str(user.pk))
    except Exception as exc:
        link = zwift_client.DisconnectOutcome.FAILED
        logfire.error("Could not disconnect Zwift on account deletion", user_id=user.pk, error=type(exc).__name__)
    # NO_LINK is the ordinary case for a rider who never connected Zwift, not a failure -- do
    # not collapse it into FAILED, or every such deletion reports itself as unfinished.
    if link is zwift_client.DisconnectOutcome.REMOVED:
        zauth_disconnected = True
    elif link is zwift_client.DisconnectOutcome.NO_LINK:
        zauth_disconnected = False
    elif link is zwift_client.DisconnectOutcome.FAILED:
        zauth_disconnected = False
        zauth_error = "the Zwift service could not be reached"
    elif user.zwid_verification_method == user.VerificationMethod.ZAUTH:
        # Not configured here, yet the account says it was verified through a link, so a
        # link probably exists in the service and nothing could ask for it to go.
        zauth_error = "the Zwift service is not configured"
    if zauth_error:
        logfire.error("Could not disconnect Zwift on account deletion", user_id=user.pk, reason=zauth_error)

    # Neither of these is reached by the cascade. MembershipApplication has no FK to User
    # at all -- it is keyed by discord_id, and holds a complete second copy of the profile
    # (name, email, birth year, gender, country, zwid, plus the raw Discord payload).
    # GuildMember.user is SET_NULL, so the row would survive carrying the whole Discord
    # identity. Both are matched on discord_id as well as the FK, so an account that moved
    # to a new Discord login takes its older record with it.
    applications_deleted = 0
    application_ids: list[str] = []
    guild_members_deleted = 0
    application_links = {"attempted": 0, "removed": 0, "failed": 0}
    if user.discord_id:
        applications = MembershipApplication.objects.filter(discord_id=user.discord_id)
        # Kept for the audit: once the row is gone, its id is the only key to a link the
        # service may still hold for it.
        application_ids = sorted(str(pk) for pk in applications.values_list("pk", flat=True))
        # A registration connects Zwift under its own UUID, so its link is separate from the
        # account's and has to be dropped before the row that names it is gone.
        application_links = release_application_zwift_links(applications)
        applications_deleted = applications.count()
        applications.delete()
        stale_members = GuildMember.objects.filter(Q(user=user) | Q(discord_id=user.discord_id))
    else:
        stale_members = GuildMember.objects.filter(user=user)
    guild_members_deleted = stale_members.count()
    stale_members.delete()

    audit = {
        "user_id": user.pk,
        "discord_id": user.discord_id,
        "zwid": user.zwid,
        "verification_records": user.race_ready_records.count(),
        "event_signups": user.event_signups.count(),
        "membership_applications_deleted": applications_deleted,
        # discord_id is unique on MembershipApplication, so this is at most one id: the one to
        # look up in the Zwift service when application_zwift_links_failed is set.
        "membership_application_ids": application_ids,
        "guild_members_deleted": guild_members_deleted,
        "media_purged": media["purged"],
        # Anything that failed is now genuinely orphaned, so the path is the only trace
        # left of it -- see the orphaned-media sweep in TODO.md.
        "media_purge_failed": media["failed"],
        "orphaned_media_files": media["failed_files"],
        "zauth_disconnected": zauth_disconnected,
        # Whether the account's own link may still be in the service, under user_id.
        "account_zwift_link_failed": bool(zauth_error),
        "application_zwift_links_removed": application_links["removed"],
        "application_zwift_links_failed": application_links["failed"],
        "deleted_by_id": getattr(deleted_by, "pk", None),
        "self_serve": deleted_by is None or deleted_by.pk == user.pk,
    }

    # Whether the erasure actually finished. The steps above are deliberately allowed to fail
    # without blocking the deletion, which is the right call -- but it means a partial erasure
    # and a clean one are otherwise indistinguishable, and the person has been told their data
    # is gone either way. That gap is what this closes.
    #
    # A transaction is not the fix and would make it worse: the storage purge and the zauth
    # call reach outside this database and cannot be rolled back, so wrapping the function
    # would undo the rows while leaving the blobs and the upstream link deleted -- less
    # consistent than now, while looking safer.
    incomplete = []
    if media["failed"]:
        incomplete.append(f"{media['failed']} verification file(s) still in storage")
    if zauth_error:
        incomplete.append("the upstream Zwift link could not be dropped")
    if application_links["failed"]:
        incomplete.append("the Zwift link made from the membership registration could not be dropped")
    audit["complete"] = not incomplete
    audit["incomplete_reasons"] = incomplete

    user.delete()
    if incomplete:
        # Deliberately an error, not an info with a flag on it: an unfinished erasure is a
        # standing obligation, and it has to be findable without knowing to look for it.
        logfire.error("User account deleted, but the erasure did not finish", **audit)
    else:
        logfire.info("User account deleted", **audit)
    return audit


# What a sync writes to a GuildMember row it already has. Never date_left: clearing a
# departure is a separate, conditional update (see apply_guild_member_sync).
_GUILD_MEMBER_SYNC_FIELDS = [
    "username",
    "display_name",
    "nickname",
    "avatar_hash",
    "roles",
    "joined_at",
    "is_bot",
    "user",
    "date_modified",
]


def apply_guild_member_sync(
    members: list[dict[str, Any]],
    *,
    source: str = "unknown",
    authoritative: bool = False,
    allow_mass_departure: bool = False,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile the GuildMember table against a Discord member list.

    Members present in ``members`` are upserted (and linked to a ``User`` by
    ``discord_id`` when possible), which also clears the departure stamp of anyone listed
    whose stamp is older than ``observed_at``. A stamp at or after it -- a departure the bot
    reported after the list was read (:func:`record_member_departure`) -- is kept and counted
    in ``rejoin_deferred``; the next list judges it. Idempotent: re-running with the same
    input is a no-op apart from refreshing ``date_modified``.

    Only an ``authoritative`` list is also used to decide who has gone, because a departure
    ends that rider's access (``apps.accounts.membership``). For such a list, members
    previously active but missing from it are marked left, each with a low-priority
    Membership ticket (:func:`apps.tickets.services.create_member_left_ticket`), and then
    every Discord-linked account no sync has ever listed is recorded as departed
    (:func:`_record_unseen_accounts`). Each step is held back when the list does not look
    whole: always for an empty list, and when the step would take more than
    ``MASS_DEPARTURE_FLOOR`` accounts and ``MASS_DEPARTURE_SHARE`` of its population at
    once, unless ``allow_mass_departure`` says an admin has confirmed it. A refused step
    keeps the upserts, logs an error and opens (or refreshes) one Membership ticket that
    tells an admin what to do (:func:`apps.tickets.services.open_sync_refusal_ticket`).

    Args:
        members: Normalized list of member dicts (see :func:`fetch_guild_members_from_discord`).
        source: Free-form label captured in the audit log to identify which
            caller drove the sync (``"discord_api"``, ``"bot_webhook"``, etc.).
        authoritative: ``members`` is the guild's complete member list, so anyone it leaves
            out has left -- true of the paginated REST fetch, which raises rather than
            return a list missing a page. Leave it False for a list that may be partial,
            such as the bot's gateway cache, which can miss member chunks after a restart.
        allow_mass_departure: Act on an authoritative list even past the mass-departure
            limit. Does not apply to an empty list, which is never acted on.
        observed_at: When ``members`` was read from Discord; defaults to now. A row changed,
            or an account created or signed in, at or after it is left for the next run, and
            a departure stamped at or after it is not cleared.

    Returns:
        Dict with ``created``, ``updated``, ``rejoined``, ``rejoin_deferred``, ``left``, ``linked``,
        ``relinked``, ``failed``, ``unseen_recorded``, ``departures_skipped``,
        ``departures_deferred``, ``unseen_skipped``, ``total_received`` and
        ``total_active`` counts; ``departures_evaluated``, whether the list was used to
        decide who has gone; and ``departures_refused`` and ``unseen_refused``, each
        ``"empty_member_list"``, ``"mass_departure"``, or ``""`` when that step was not
        held back.

    """
    # Local imports avoid a circular dependency at module import time.
    from apps.accounts.models import GuildMember, User

    observed_at = observed_at or timezone.now()
    received_discord_ids = {m["discord_id"] for m in members if m.get("discord_id")}
    existing_discord_ids = set(
        GuildMember.objects.filter(date_left__isnull=True).values_list("discord_id", flat=True)
    )
    users_by_discord_id = {u.discord_id: u for u in User.objects.filter(discord_id__in=received_discord_ids)}

    created = 0
    updated = 0
    rejoined = 0
    rejoin_deferred = 0
    linked = 0
    relinked = 0
    failed = 0

    for member_data in members:
        try:
            joined_at: datetime | None = None
            raw_joined = member_data.get("joined_at")
            if raw_joined:
                with contextlib.suppress(ValueError):
                    joined_at = datetime.fromisoformat(str(raw_joined))

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

                if not existing.user and member_data["discord_id"] in users_by_discord_id:
                    candidate = users_by_discord_id[member_data["discord_id"]]
                    relinked += _release_user_link(candidate, keep_pk=existing.pk)
                    existing.user = candidate
                    linked += 1

                # date_left is never written here: a departure the bot reported
                # (record_member_departure) between the read above and this save would
                # otherwise be overwritten with the value read before it.
                existing.save(update_fields=_GUILD_MEMBER_SYNC_FIELDS)

                if was_left:
                    # Clear the stamp only when the list is newer than it. A stamp at or after
                    # observed_at -- the bot saw them leave after this list was read -- is
                    # newer news than the list, so it stays for the next list to judge.
                    if GuildMember.objects.filter(pk=existing.pk, date_left__lt=observed_at).update(
                        date_left=None, date_modified=timezone.now()
                    ):
                        existing.date_left = None
                        rejoined += 1
                    else:
                        rejoin_deferred += 1
                else:
                    updated += 1
            else:
                user = users_by_discord_id.get(member_data["discord_id"])
                relinked += _release_user_link(user)
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
        except Exception as exc:
            # One unusable member must not abort the sweep: before this, a single
            # bad row meant zero members updated, no departures stamped, and the
            # GuildMember cache silently frozen until someone noticed.
            failed += 1
            logfire.error(
                "Failed to sync one guild member",
                source=source,
                discord_id=member_data.get("discord_id"),
                error=str(exc),
            )

    left = 0
    departures_refused = ""
    departures_skipped = 0
    departures_deferred = 0
    unseen_recorded = 0
    unseen_refused = ""
    unseen_skipped = 0
    if authoritative:
        departing = GuildMember.objects.filter(
            discord_id__in=existing_discord_ids - received_discord_ids,
            date_left__isnull=True,
        )
        # A row changed since the list was read carries newer news than the list: a rider
        # who rejoined and signed in meanwhile (clear_departure stamps date_modified), or a
        # member the bot push added. Stamping it would sign that rider straight back out,
        # so the next run judges it against a newer list.
        departures_deferred = departing.filter(date_modified__gte=observed_at).count()
        departed = list(departing.filter(date_modified__lt=observed_at))
        departures_refused = _departure_refusal(
            received=len(received_discord_ids),
            departing=len(departed),
            active=len(existing_discord_ids),
            allow_mass_departure=allow_mass_departure,
        )
        if departures_refused:
            departures_skipped = len(departed)
            logfire.error(
                "Guild member sync refused to stamp departures",
                source=source,
                reason=departures_refused,
                departing=departures_skipped,
                active_before=len(existing_discord_ids),
                total_received=len(members),
            )
        else:
            left = _stamp_departures(departed, observed_at=observed_at)
            # Only reached once the list has passed the departure check: a list too short to
            # trust for departures is too short to say who was never in the server.
            unseen = _unseen_accounts(received_discord_ids, observed_at=observed_at)
            # Measured against the Discord-linked accounts, not the active rows: those are
            # the population this step signs out, and a sparse GuildMember table (a fresh or
            # restored database, rows deleted in the admin) would make the active count
            # meaningless.
            unseen_refused = _departure_refusal(
                received=len(received_discord_ids),
                departing=len(unseen),
                active=User.objects.exclude(discord_id="").count(),
                allow_mass_departure=allow_mass_departure,
            )
            if unseen_refused:
                unseen_skipped = len(unseen)
                logfire.error(
                    "Guild member sync refused to record accounts it has never listed",
                    source=source,
                    reason=unseen_refused,
                    unseen=unseen_skipped,
                    total_received=len(members),
                )
            else:
                unseen_recorded = _record_unseen_accounts(unseen)
        if departures_refused or unseen_refused:
            _report_sync_refusal(
                departures_refused=departures_refused,
                departures_skipped=departures_skipped,
                unseen_refused=unseen_refused,
                unseen_skipped=unseen_skipped,
                total_received=len(received_discord_ids),
                active_before=len(existing_discord_ids),
            )

    total_active = GuildMember.objects.filter(date_left__isnull=True).count()

    logfire.info(
        "Guild members synced",
        source=source,
        created=created,
        updated=updated,
        rejoined=rejoined,
        rejoin_deferred=rejoin_deferred,
        left=left,
        linked=linked,
        relinked=relinked,
        failed=failed,
        unseen_recorded=unseen_recorded,
        departures_evaluated=authoritative,
        departures_refused=departures_refused,
        departures_skipped=departures_skipped,
        departures_deferred=departures_deferred,
        unseen_refused=unseen_refused,
        unseen_skipped=unseen_skipped,
        total_received=len(members),
        total_active=total_active,
    )

    return {
        "created": created,
        "updated": updated,
        "rejoined": rejoined,
        "rejoin_deferred": rejoin_deferred,
        "left": left,
        "linked": linked,
        "relinked": relinked,
        "failed": failed,
        "unseen_recorded": unseen_recorded,
        "departures_evaluated": authoritative,
        "departures_refused": departures_refused,
        "departures_skipped": departures_skipped,
        "departures_deferred": departures_deferred,
        "unseen_refused": unseen_refused,
        "unseen_skipped": unseen_skipped,
        "total_received": len(members),
        "total_active": total_active,
    }


def _departure_refusal(*, received: int, departing: int, active: int, allow_mass_departure: bool) -> str:
    """Say why a sync must not sign these accounts out, if it must not.

    Used for both steps that sign people out: stamping departures and recording accounts no
    sync has ever listed.

    Args:
        received: Distinct Discord ids in the member list.
        departing: Accounts the step would sign out.
        active: The population they come from -- active rows before the sync, or
            Discord-linked accounts.
        allow_mass_departure: An admin has confirmed that a large departure is real.

    Returns:
        ``"empty_member_list"`` or ``"mass_departure"``, or ``""`` if the step may go ahead.

    """
    # Never real: the bot the list comes from is itself a member, so no confirmation makes
    # an empty list whole -- and it would sign out every rider at once.
    if received == 0:
        return "empty_member_list"
    if not allow_mass_departure and departing > max(MASS_DEPARTURE_FLOOR, active * MASS_DEPARTURE_SHARE):
        return "mass_departure"
    return ""


def _stamp_departures(rows: list, *, observed_at: datetime) -> int:
    """Mark these GuildMember rows as departed and file a member-left ticket for each.

    Iterates rather than bulk-updating so each departure gets the ticket admins follow up.
    Each stamp is conditional: a row that came back, or was touched, since it was read
    (``clear_departure`` after a live login) is left for the next run.

    Args:
        rows: Active rows the member list left out, read before the stamp.
        observed_at: When the member list was read.

    Returns:
        The number of rows stamped.

    """
    from apps.accounts.models import GuildMember
    from apps.tickets.services import create_member_left_ticket

    now = timezone.now()
    stamped = 0
    for gm in rows:
        changed = GuildMember.objects.filter(
            pk=gm.pk,
            date_left__isnull=True,
            date_modified__lt=observed_at,
        ).update(date_left=now, date_modified=now)
        if not changed:
            continue
        stamped += 1
        gm.date_left = now
        gm.date_modified = now
        try:
            create_member_left_ticket(gm)
        except Exception as exc:
            logfire.error(
                "Failed to create member-left ticket",
                guild_member_id=gm.pk,
                discord_id=gm.discord_id,
                error=str(exc),
            )
    return stamped


def is_discord_snowflake(value: object) -> bool:
    """Say whether a value is a Discord user id as the bot sends one.

    Args:
        value: The value to check.

    Returns:
        True for a string of 1-20 ASCII digits.

    """
    return isinstance(value, str) and DISCORD_SNOWFLAKE_RE.fullmatch(value) is not None


def _clip(value: object, field_name: str) -> str:
    """Fit a bot-reported string into a GuildMember field.

    Args:
        value: The reported value; None or blank reads as "".
        field_name: The GuildMember field it is stored in.

    Returns:
        The value, cut to the field's ``max_length``.

    """
    from apps.accounts.models import GuildMember

    return str(value or "")[: GuildMember._meta.get_field(field_name).max_length]


def _create_departed_member(discord_id: str, member_data: dict[str, Any]) -> GuildMember:
    """Create the GuildMember row for a member the bot saw leave before any sync listed them.

    The user link follows the rules of :func:`_record_unseen_accounts`: only when exactly one
    account holds this Discord id and that account has no row of its own yet. An account
    already linked keeps its link (it may be an older Discord account's row), and a shared id
    is recorded linked to nobody rather than to a guess. The access rule reads the row by
    ``discord_id`` either way.

    The row is created departed, with ``date_left`` strictly after ``date_created``: the bot
    watched this member leave, so it must not read as "never seen in the server"
    (:func:`never_seen_in_guild`).

    Args:
        discord_id: The departed member's Discord id.
        member_data: What the bot knows about them (``username``, ``display_name``,
            ``avatar_hash``, ``is_bot``); anything absent is left blank.

    A row for this Discord id created meanwhile (by a sync or another report) makes the
    insert raise ``IntegrityError``, from a savepoint, so an enclosing transaction survives.

    Returns:
        The new row.

    """
    from django.db import transaction

    from apps.accounts.models import GuildMember, User

    holders = list(User.objects.filter(discord_id=discord_id).values_list("pk", flat=True)[:2])
    link = None
    if len(holders) == 1 and not GuildMember.objects.filter(user_id=holders[0]).exists():
        link = holders[0]

    with transaction.atomic():
        row = GuildMember.objects.create(
            discord_id=discord_id,
            username=_clip(member_data.get("username"), "username"),
            display_name=_clip(member_data.get("display_name"), "display_name"),
            avatar_hash=_clip(member_data.get("avatar_hash"), "avatar_hash"),
            is_bot=bool(member_data.get("is_bot")),
            user_id=link,
            # Departed from the start; the final stamp is set below, inside the same savepoint,
            # so no other connection ever sees the row active or never-seen.
            date_left=timezone.now(),
        )
        # date_created is auto_now_add and cannot be passed in; stamp strictly after it.
        stamp = max(timezone.now(), row.date_created + timedelta(microseconds=1))
        GuildMember.objects.filter(pk=row.pk).update(date_left=stamp, date_modified=stamp)
    row.date_left = stamp
    row.date_modified = stamp
    return row


def record_member_departure(
    discord_id: str,
    member_data: dict[str, Any] | None = None,
    *,
    source: str = "bot_event",
) -> dict[str, Any]:
    """Record at once that a member has left the guild, as the Discord bot reports it.

    The bot sees a member leave the moment it happens, so this closes the gap the scheduled
    sync leaves (up to ``SCHEDULER_SYNC_GUILD_MEMBERS_HOURS``): the stamp signs a non-staff
    rider out on their next request and stops their API keys (``apps.accounts.membership``).
    One member per call, so no mass-departure check applies.

    - An active row is stamped (conditionally, so a concurrent stamp is not repeated) and a
      member-left ticket is filed, as the sync does.
    - A row already departed keeps its departure, but the stamp is moved up to now, with no
      ticket. The member may have rejoined and left again: a sync that read its list in
      between still lists them, and would otherwise clear the old stamp after this report. A
      "never seen" row (:func:`never_seen_in_guild`) becomes a real departure this way,
      which is what it is: the bot saw the member leave.
    - With no row, one is created from ``member_data`` and stamped (see
      :func:`_create_departed_member`). The ticket is filed only when some account holds the
      Discord id. For anyone else there is nothing in the app to follow up, the scheduled
      sync never files one for them either, and join-and-leave churn would otherwise fill
      the queue.

    A later sync clears the stamp only if its member list was read after it
    (``observed_at`` in :func:`apply_guild_member_sync`), and a Discord login that passes the
    live guild check clears it (``apps.accounts.membership.clear_departure``).

    Args:
        discord_id: The departed member's Discord id (see :func:`is_discord_snowflake`).
        member_data: Optional ``username``, ``display_name``, ``avatar_hash`` and ``is_bot``,
            used only to create a missing row.
        source: Label for the audit log.

    Returns:
        ``{"status": "departed" | "already_departed", "created": bool,
        "ticket_created": bool}``.

    Raises:
        ValueError: ``discord_id`` is not a Discord id.
        IntegrityError: Creating the missing row clashed with a row that then could not be
            found (deleted again at once).

    """
    from django.db import IntegrityError

    from apps.accounts.models import GuildMember, User
    from apps.tickets.services import create_member_left_ticket

    if not is_discord_snowflake(discord_id):
        msg = "discord_id must be 1-20 ASCII digits"
        raise ValueError(msg)

    created = False
    row = GuildMember.objects.filter(discord_id=discord_id).first()
    if row is None:
        try:
            row = _create_departed_member(discord_id, member_data or {})
            created = True
        except IntegrityError:
            # Created meanwhile by a sync or a repeated report: stamp that row instead.
            row = GuildMember.objects.filter(discord_id=discord_id).first()
            if row is None:
                raise

    if not created:
        now = timezone.now()
        # The departed case goes first. The other order would leave a gap: the "still active?"
        # update misses, a sync clears the old stamp, and the "move it up" update then misses
        # too, so the report would answer already_departed for a row that is active. In this
        # order the only thing that can land between the two updates is another stamp.
        moved = GuildMember.objects.filter(pk=row.pk, date_left__lt=now).update(date_left=now, date_modified=now)
        if moved or not GuildMember.objects.filter(pk=row.pk, date_left__isnull=True).update(
            date_left=now, date_modified=now
        ):
            logfire.info(
                "Member departure already recorded",
                source=source,
                discord_id=discord_id,
                guild_member_id=row.pk,
                user_id=row.user_id,
                stamp_moved=bool(moved),
            )
            return {"status": "already_departed", "created": False, "ticket_created": False}
        row.date_left = now
        row.date_modified = now

    ticket_created = False
    # A new row nobody's account holds the id of: nothing in the app to follow up (see above).
    no_account = created and row.user_id is None and not User.objects.filter(discord_id=discord_id).exists()
    if not no_account:
        try:
            ticket_created = create_member_left_ticket(row) is not None
        except Exception as exc:
            logfire.error(
                "Failed to create member-left ticket",
                guild_member_id=row.pk,
                discord_id=discord_id,
                error=str(exc),
            )

    logfire.info(
        "Recorded a guild member departure",
        source=source,
        discord_id=discord_id,
        guild_member_id=row.pk,
        user_id=row.user_id,
        created=created,
        ticket_created=ticket_created,
    )
    return {"status": "departed", "created": created, "ticket_created": ticket_created}


def _report_sync_refusal(**counts: Any) -> None:
    """Open or refresh the ticket that tells an admin the sync held sign-outs back.

    A failure here must not undo the sync, which has already saved what it received.

    Args:
        **counts: Passed to :func:`apps.tickets.services.open_sync_refusal_ticket`.

    """
    from apps.tickets.services import open_sync_refusal_ticket

    try:
        open_sync_refusal_ticket(**counts)
    except Exception as exc:
        logfire.error("Failed to open the guild sync refusal ticket", error=str(exc))


def describe_sync_refusal(result: dict[str, Any]) -> str:
    """Say what a refused guild sync held back and what an admin should do about it.

    Args:
        result: The dict :func:`apply_guild_member_sync` returned.

    Returns:
        One or two sentences, or ``""`` if nothing was refused.

    """
    if result.get("departures_refused") == "empty_member_list":
        return (
            "Guild member sync got an empty member list from Discord and marked nobody as left. "
            "Accepting a mass departure does not apply to an empty list: check DISCORD_BOT_TOKEN, "
            "GUILD_ID and the bot's Server Members intent."
        )
    held = []
    if result.get("departures_refused"):
        held.append(f"{result['departures_skipped']} departure(s)")
    if result.get("unseen_refused"):
        held.append(f"{result['unseen_skipped']} account(s) no sync has ever listed")
    if not held:
        return ""
    return (
        f"Guild member sync held back {' and '.join(held)}: more than its limit at once. "
        f"The {result['total_received']} members it received were saved. If this is real, run "
        "sync_guild_members from /site/config/background_tasks/ with 'Accept a mass departure' ticked."
    )


def never_seen_in_guild() -> Q:
    """Match the GuildMember rows recorded for an account no guild sync has ever listed.

    :func:`_record_unseen_accounts` creates those rows already departed, with ``date_left``
    copied from ``date_created``. A real departure is stamped on a row an earlier sync
    created as active, so its ``date_left`` is always later. Once such an account turns up
    in a sync, or passes a live login check, the stamp is cleared, and a later departure is
    a real one. A leave the bot reports for such a row moves its stamp later
    (:func:`record_member_departure`), which also makes it a real departure.

    Returns:
        A filter for ``GuildMember`` querysets.

    """
    return Q(date_left__isnull=False, date_left=F("date_created"))


def is_never_seen(member: GuildMember) -> bool:
    """Say whether one GuildMember row is a never-seen record (see :func:`never_seen_in_guild`).

    Args:
        member: The row.

    Returns:
        True for a row recorded for an account no guild sync has ever listed.

    """
    return member.date_left is not None and member.date_left == member.date_created


def annotate_never_seen(queryset: QuerySet) -> QuerySet:
    """Add a ``never_seen`` flag (see :func:`never_seen_in_guild`) to GuildMember rows.

    Args:
        queryset: A ``GuildMember`` queryset.

    Returns:
        The queryset, each row carrying ``never_seen``.

    """
    return queryset.annotate(never_seen=ExpressionWrapper(never_seen_in_guild(), output_field=BooleanField()))


def _unseen_accounts(received_discord_ids: set[str], *, observed_at: datetime) -> list[tuple[int, str, str, bool]]:
    """List the Discord-linked accounts that no guild sync has ever listed.

    Skips accounts created or signed in at or after ``observed_at``: that login's live guild
    check is newer than the list. Skips ids in this list too -- they got a row above unless
    their upsert failed, which is not a departure.

    Args:
        received_discord_ids: The Discord ids in this sync's member list.
        observed_at: When the member list was read.

    Returns:
        ``(user_id, discord_id, discord_username, already_linked)`` per account, by pk.

    """
    from django.db.models import Exists, OuterRef

    from apps.accounts.models import GuildMember, User

    candidates = (
        User.objects
        .exclude(discord_id="")
        .exclude(Exists(GuildMember.objects.filter(discord_id=OuterRef("discord_id"))))
        .exclude(date_joined__gte=observed_at)
        .exclude(last_login__gte=observed_at)
        .annotate(already_linked=Exists(GuildMember.objects.filter(user=OuterRef("pk"))))
        .order_by("pk")
        .values_list("pk", "discord_id", "discord_username", "already_linked")
    )
    return [row for row in candidates if row[1] not in received_discord_ids]


def _record_unseen_accounts(accounts: list[tuple[int, str, str, bool]]) -> int:
    """Record as departed every Discord-linked account that no guild sync has ever listed.

    ``has_left_guild`` reads "no row" as "no verdict yet", but the sweep only creates rows for
    members it receives and only stamps rows it already has. Somebody who signed in and left
    the server before a sync saw them -- or whose ``discord_id`` moved to a Discord account
    that left before then -- would otherwise never get a row, and keep their access and API
    keys for good. A departed row closes that. No ticket is filed: nobody saw them as a
    member, so there is no departure to follow up, and the first run after this shipped
    would otherwise file one for every such account at once.

    Each row is created already departed, ``date_left`` equal to ``date_created``, which is
    how :func:`never_seen_in_guild` tells these rows from real departures.

    Only called for an authoritative member list that has passed ``_departure_refusal``,
    for both its departures and these accounts.

    Args:
        accounts: What :func:`_unseen_accounts` returned.

    Returns:
        The number of rows created.

    """
    from collections import Counter

    from django.db import IntegrityError, transaction

    from apps.accounts.models import GuildMember

    # User.discord_id is not unique. The row is still recorded (the rule reads it by id), but
    # linked to nobody rather than to a guess.
    holders = Counter(discord_id for _, discord_id, _, _ in accounts)
    recorded: list[int] = []
    done: set[str] = set()
    for user_id, discord_id, discord_username, already_linked in accounts:
        if discord_id in done:
            continue
        done.add(discord_id)
        link = None if already_linked or holders[discord_id] > 1 else user_id
        try:
            # A savepoint, so a clash with a concurrent sync (every web replica runs a
            # scheduler) cannot break an enclosing transaction; it also keeps the row from
            # being seen before its marker is set.
            with transaction.atomic():
                row = GuildMember.objects.create(
                    discord_id=discord_id,
                    username=discord_username or "",
                    user_id=link,
                    date_left=timezone.now(),
                )
                # date_created is auto_now_add, so it cannot be passed in; copy it instead.
                GuildMember.objects.filter(pk=row.pk).update(date_left=F("date_created"))
        except IntegrityError as exc:
            logfire.warning(
                "Could not record an account no guild sync has listed",
                user_id=user_id,
                discord_id=discord_id,
                error=str(exc),
            )
            continue
        recorded.append(user_id)

    if recorded:
        logfire.info(
            "Recorded accounts no guild sync has listed as departed",
            count=len(recorded),
            user_ids=recorded[:100],
        )
    return len(recorded)
