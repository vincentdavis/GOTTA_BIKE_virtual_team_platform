"""Service functions for accounts app."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
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
                existing.date_left = None  # Clear when they're back

                if not existing.user and member_data["discord_id"] in users_by_discord_id:
                    candidate = users_by_discord_id[member_data["discord_id"]]
                    relinked += _release_user_link(candidate, keep_pk=existing.pk)
                    existing.user = candidate
                    linked += 1

                existing.save()

                if was_left:
                    rejoined += 1
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
        relinked=relinked,
        failed=failed,
        total_received=len(members),
        total_active=total_active,
    )

    return {
        "created": created,
        "updated": updated,
        "rejoined": rejoined,
        "left": left,
        "linked": linked,
        "relinked": relinked,
        "failed": failed,
        "total_received": len(members),
        "total_active": total_active,
    }
