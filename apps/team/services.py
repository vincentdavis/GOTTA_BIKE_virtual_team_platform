"""Service layer for unified team roster operations."""

import hashlib
import hmac
import inspect
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import logfire
from constance import config
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Max, Min, OuterRef, Q, Subquery
from django.utils import timezone

from apps.accounts.models import GuildMember, User
from apps.team.models import RaceReadyRecord, RecordView
from apps.zwiftpower.models import ZPRiderResults, ZPTeamRiders
from apps.zwiftracing.models import ZRRider

# Default verification types when no ZwiftPower category is found
DEFAULT_VERIFICATION_TYPES: list[str] = ["weight_light", "height"]

# ZwiftPower division to category letter mapping
ZP_DIV_TO_CATEGORY: dict[int, str] = {
    5: "A+",
    10: "A",
    20: "B",
    30: "C",
    40: "D",
    50: "E",
}


def verification_accepted(user_row: dict) -> bool:
    """Apply the zauth cutover policy to a ``.values()`` roster row.

    Mirrors ``User.has_accepted_zwid_verification``. That property cannot be reused
    here because these rosters read ``.values()`` dicts for efficiency rather than
    model instances, and a Python property is invisible to the ORM.

    Args:
        user_row: A row carrying ``zwid_verified`` and ``zwid_verification_method``.

    Returns:
        Whether the row's verification counts under the active policy.

    """
    if not user_row.get("zwid_verified"):
        return False
    if not config.ZAUTH_VERIFICATION_REQUIRED:
        return True
    return user_row.get("zwid_verification_method") == "zauth"


def get_user_required_verification_types(user: User) -> list[str]:
    """Get required verification types for race-ready status based on ZwiftPower category.

    Args:
        user: The user to get required types for.

    Returns:
        List of verify_type values required for race-ready status.

    """
    if not user.zwid:
        return DEFAULT_VERIFICATION_TYPES

    zp_rider = ZPTeamRiders.objects.filter(zwid=user.zwid).first()
    if not zp_rider:
        return DEFAULT_VERIFICATION_TYPES

    category = zp_rider.divw if user.gender == "female" else zp_rider.div
    if not category:
        return DEFAULT_VERIFICATION_TYPES

    try:
        requirements = json.loads(config.CATEGORY_REQUIREMENTS)
        types = requirements.get(str(category), DEFAULT_VERIFICATION_TYPES)
        return types if types else DEFAULT_VERIFICATION_TYPES
    except (json.JSONDecodeError, TypeError) as e:
        logfire.error(
            "Failed to parse CATEGORY_REQUIREMENTS config",
            error=str(e),
            user_id=user.id,
            zwid=user.zwid,
        )
        return DEFAULT_VERIFICATION_TYPES


_VERIFICATION_GROUPS = {
    "weight": {"weight_full", "weight_light"},
    "height": {"height"},
    "power": {"power"},
}
_ALL_VERIFY_TYPES = ["weight_full", "weight_light", "height", "power"]


def verification_days_bulk(users: list[User]) -> dict[int, dict]:
    """Compute verification day-counts for many users with a fixed number of queries.

    Batched equivalent of computing per-user weight/height/power/race-ready days.
    Uses a small constant number of queries (verified records, ZwiftPower
    categories, and the validity settings read once) regardless of user count,
    instead of several queries per user, then does the date math in memory.

    Args:
        users: The users to compute for.

    Returns:
        Mapping of ``user.id`` -> dict with ``weight_days``, ``height_days``,
        ``power_days``, ``race_ready_days``, ``has_height`` (same shape as the
        per-user computation).

    """
    user_list = list(users)
    user_ids = [u.id for u in user_list]

    # 1) Latest verified record per (user, group). Ordered newest-first so the
    # first record seen for each group is the latest.
    latest: dict[int, dict[str, RaceReadyRecord]] = {}
    records = (
        RaceReadyRecord.objects.filter(
            user_id__in=user_ids,
            status=RaceReadyRecord.Status.VERIFIED,
            verify_type__in=_ALL_VERIFY_TYPES,
        )
        .order_by("user_id", "-record_date")
    )
    for rec in records:
        groups = latest.setdefault(rec.user_id, {})
        for group, types in _VERIFICATION_GROUPS.items():
            if rec.verify_type in types:
                groups.setdefault(group, rec)

    # 2) ZwiftPower categories for the required-types lookup, parsed config once.
    zwids = [u.zwid for u in user_list if u.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    try:
        requirements = json.loads(config.CATEGORY_REQUIREMENTS)
    except (json.JSONDecodeError, TypeError):
        requirements = {}

    # Read the validity settings ONCE (RaceReadyRecord.days_remaining reads all four
    # from Constance on every call, which is the real per-record query cost).
    today = timezone.now().date()
    validity_days = {
        "weight_full": config.WEIGHT_FULL_DAYS,
        "weight_light": config.WEIGHT_LIGHT_DAYS,
        "height": config.HEIGHT_VERIFICATION_DAYS,
        "power": config.POWER_VERIFICATION_DAYS,
    }

    def _days_and_has(rec: RaceReadyRecord | None) -> tuple[int | None, bool]:
        if rec is None:
            return None, False
        validity = validity_days.get(rec.verify_type, 0)
        if not validity or not rec.record_date:
            return None, True  # never expires, but a record exists
        days = (rec.record_date + timedelta(days=validity) - today).days
        if days < 0:
            return None, True  # expired but a record exists
        return days, True

    def _required_types(user: User) -> list[str]:
        zp = zp_by_zwid.get(user.zwid) if user.zwid else None
        if not zp:
            return DEFAULT_VERIFICATION_TYPES
        category = zp.divw if user.gender == "female" else zp.div
        if not category:
            return DEFAULT_VERIFICATION_TYPES
        types = requirements.get(str(category), DEFAULT_VERIFICATION_TYPES)
        return types if types else DEFAULT_VERIFICATION_TYPES

    result: dict[int, dict] = {}
    for user in user_list:
        groups = latest.get(user.id, {})
        weight_days, _ = _days_and_has(groups.get("weight"))
        height_days, has_height = _days_and_has(groups.get("height"))
        power_days, _ = _days_and_has(groups.get("power"))

        race_ready_days: int | None = None
        if user.is_race_ready:
            type_to_days = {
                "weight_full": weight_days,
                "weight_light": weight_days,
                "height": height_days,
                "power": power_days,
            }
            constraining = [type_to_days[t] for t in _required_types(user) if type_to_days.get(t) is not None]
            if constraining:
                race_ready_days = min(constraining)

        result[user.id] = {
            "weight_days": weight_days,
            "height_days": height_days,
            "power_days": power_days,
            "race_ready_days": race_ready_days,
            "has_height": has_height,
        }
    return result


def get_user_verification_types(user: User) -> list[str]:
    """Get all verification types a user is allowed to submit.

    Starts with the required types from CATEGORY_REQUIREMENTS, then adds:
    - ``power`` — always available for any rider to submit.
    - ``weight_light`` — available if the rider has a verified weight_full record.

    Args:
        user: The user to get submittable types for.

    Returns:
        List of verify_type values the user may submit.

    """
    types = list(get_user_required_verification_types(user))

    # Power verification is always available (not required for all categories, but any rider can submit)
    if "power" not in types:
        types.append("power")

    # Riders with a verified weight_full can also submit weight_light
    if "weight_light" not in types:
        from apps.team.models import RaceReadyRecord

        has_verified_weight = user.race_ready_records.filter(
            verify_type="weight_full",
            status=RaceReadyRecord.Status.VERIFIED,
        ).exists()
        if has_verified_weight:
            types.append("weight_light")

    return types


# Canonical display order and labels for the four verification types.
VERIFY_TYPE_LABELS: dict[str, str] = {
    "weight_full": "Weight (Full)",
    "weight_light": "Weight (Light)",
    "height": "Height",
    "power": "Power",
}


def _verify_type_validity_days() -> dict[str, int]:
    """Map each verify_type to its configured validity window in days.

    Returns:
        Dict of verify_type to validity days (0 means never expires).

    """
    return {
        "weight_full": config.WEIGHT_FULL_DAYS,
        "weight_light": config.WEIGHT_LIGHT_DAYS,
        "height": config.HEIGHT_VERIFICATION_DAYS,
        "power": config.POWER_VERIFICATION_DAYS,
    }


def _current_verification_status(record: RaceReadyRecord | None) -> dict[str, str]:
    """Summarize the latest record of a type for the submission picker.

    Args:
        record: The user's most recent record of a given verify_type, or None.

    Returns:
        Dict with ``state`` (valid/expired/pending/rejected/none) and human ``text``.

    """
    if record is None:
        return {"state": "none", "text": "None on file"}
    if record.is_verified:
        if record.is_expired:
            days = record.days_remaining
            ago = f"{abs(days)} days ago" if days is not None else ""
            return {"state": "expired", "text": f"Expired {ago}".strip()}
        remaining = record.days_remaining
        if remaining is None:
            return {"state": "valid", "text": "Verified · no expiry"}
        return {"state": "valid", "text": f"Verified · {remaining} days left"}
    if record.is_rejected:
        return {"state": "rejected", "text": "Last submission rejected"}
    return {"state": "pending", "text": "Pending review"}


def build_verify_type_options(user: User) -> list[dict]:
    """Build the annotated verification-type list for the submission picker.

    Each option carries the label, whether it is required for this rider's category,
    the validity window, and the state of the rider's most recent record of that type.
    Only types the rider may currently submit are included (e.g. weight_light is
    omitted until they have a verified full weight), and required types are listed first.

    Args:
        user: The rider submitting a verification.

    Returns:
        List of option dicts consumed by the race-ready form template.

    """
    allowed = get_user_verification_types(user)
    required = set(get_user_required_verification_types(user))
    validity_days = _verify_type_validity_days()

    latest_by_type: dict[str, RaceReadyRecord] = {}
    for record in user.race_ready_records.all():
        latest_by_type.setdefault(record.verify_type, record)

    def _option(vtype: str) -> dict:
        days = validity_days.get(vtype, 0)
        return {
            "value": vtype,
            "label": VERIFY_TYPE_LABELS.get(vtype, vtype),
            "required": vtype in required,
            "validity_label": "Never expires" if not days else f"Valid {days} days",
            "current": _current_verification_status(latest_by_type.get(vtype)),
        }

    ordered = [t for t in VERIFY_TYPE_LABELS if t in allowed and t in required]
    ordered += [t for t in VERIFY_TYPE_LABELS if t in allowed and t not in required]
    return [_option(t) for t in ordered]


def _has_media() -> Q:
    """Match records that still carry evidence.

    ``media_file`` is both ``null=True`` and ``blank=True``, so an empty upload can be
    stored either way and both have to be ruled out.

    Returns:
        A ``Q`` matching records with an uploaded file, an evidence URL, or both.

    """
    return Q(url__gt="") | (Q(media_file__isnull=False) & ~Q(media_file=""))


def _strip_media(records: list[RaceReadyRecord], *, reason: str) -> dict[str, int]:
    """Delete the stored file and clear the evidence URL on each record.

    One unreadable file must not abort the rest of a sweep, so storage failures are
    counted and skipped rather than raised. ``delete_media_file`` has already logged the
    error by the time it reaches here.

    Args:
        records: Records to strip. Callers are expected to have selected them already.
        reason: Why these records are being stripped, for the log line.

    Returns:
        Counts of records ``considered``, ``purged`` and ``failed``, plus ``failed_files``
        naming the storage paths that could not be deleted.

    """
    purged = 0
    failed_files = []
    for record in records:
        try:
            file_name = record.media_file.name if record.media_file else ""
            record.delete_media_file()
            record.url = ""
            # delete_media_file() drops the stored file with save=False, so media_file has
            # to be persisted here as well -- otherwise the column keeps the deleted file's
            # name and the record still looks like it has evidence. Regression covered by
            # apps/team/test_media_purge.py.
            record.save(update_fields=["url", "media_file"])
            purged += 1
        except Exception as e:
            if file_name:
                failed_files.append(file_name)
            logfire.error(
                "Could not strip verification media",
                record_id=record.id,
                user_id=record.user_id,
                reason=reason,
                error=str(e),
            )

    return {
        "considered": len(records),
        "purged": purged,
        "failed": len(failed_files),
        # The paths of anything that could not be deleted. When the caller is about to
        # drop the rows too (account deletion), this is the only trace of a file that is
        # now unreachable except by enumerating the storage prefix.
        "failed_files": failed_files,
    }


def purge_user_verification_media(user: User) -> dict:
    """Strip evidence from every verification record belonging to ``user``.

    Called just before ``User.delete()``. The cascade cannot do this itself: Django's
    Collector issues bulk deletes and never calls ``Model.delete()`` per instance, so a
    model-level override would not fire -- and once the rows are gone the files are
    unreachable except by enumerating the storage prefix.

    Records are saved as well as stripped even though they are about to be deleted. That
    is not wasted work: if the delete that follows fails, the rows are left consistent
    with storage rather than pointing at files that no longer exist.

    Args:
        user: The user whose account is being deleted.

    Returns:
        Counts of records ``considered``, ``purged`` and ``failed``, plus ``failed_files``.

    """
    records = list(user.race_ready_records.filter(_has_media()))
    result = _strip_media(records, reason="account deleted")
    logfire.info("Purged verification media for deleted account", user_id=user.pk, **result)
    return result


def delete_verification_records(user: User, record_ids) -> dict:
    """Delete verification records a rider has chosen to remove from their own history.

    Scoped to ``user``'s own records, so an id belonging to someone else simply does not
    match. Evidence is stripped from storage before the rows go, for the same reason it is
    on account deletion: Django never removes FileField storage on delete, and once the row
    is gone the file is unreachable except by enumerating the storage prefix.

    A file that cannot be deleted does not stop the row being removed -- the rider asked
    for it gone, and refusing on a storage error would leave them stuck. The path is logged
    so it can be swept later.

    Race-ready status is recalculated afterwards. Deleting the record that was covering a
    required verification type does revoke it; that is the honest consequence of removing
    the evidence, and the caller is expected to have warned the rider.

    Args:
        user: The rider deleting their own records.
        record_ids: Candidate primary keys, unfiltered and untrusted.

    Returns:
        ``deleted`` and ``failed_media`` counts, plus ``was_race_ready`` / ``is_race_ready``
        so the caller can tell the rider if this cost them their status.

    """
    # Read the live values, not the cached columns: a verification that expired since the
    # last refresh_all_race_ready sweep would otherwise make the deletion look like the
    # cause of a status loss it had nothing to do with.
    was_race_ready = user.calculate_race_ready()
    was_extra_verified = user.calculate_extra_verified()
    records = list(user.race_ready_records.filter(pk__in=record_ids))
    if not records:
        return {
            "deleted": 0,
            "failed_media": 0,
            "was_race_ready": was_race_ready,
            "is_race_ready": was_race_ready,
            "was_extra_verified": was_extra_verified,
            "is_extra_verified": was_extra_verified,
        }

    media = _strip_media([r for r in records if r.media_file or r.url], reason="deleted by rider")
    RaceReadyRecord.objects.filter(pk__in=[r.pk for r in records]).delete()
    is_race_ready, is_extra_verified = user.refresh_race_ready()

    if was_race_ready and not is_race_ready:
        # Drop the Discord race-ready role now rather than leaving the rider wearing it
        # until the nightly sync_race_ready_roles sweep notices. Deleting records can only
        # ever cost the status, never grant it, so this is the one direction to handle.
        from apps.team.tasks import notify_race_ready_change

        notify_race_ready_change.enqueue(
            user_id=user.pk,
            is_now_race_ready=False,
            changed_by_user_id=user.pk,
        )

    logfire.info(
        "Rider deleted their own verification records",
        user_id=user.pk,
        deleted=len(records),
        verify_types=sorted({r.verify_type for r in records}),
        failed_media=media["failed"],
        orphaned_media_files=media["failed_files"],
        was_race_ready=was_race_ready,
        is_race_ready=is_race_ready,
    )
    return {
        "deleted": len(records),
        "failed_media": media["failed"],
        "was_race_ready": was_race_ready,
        "is_race_ready": is_race_ready,
        "was_extra_verified": was_extra_verified,
        "is_extra_verified": is_extra_verified,
    }


def purge_expired_verification_media() -> dict[str, int]:
    """Strip evidence from verified records whose verification has expired.

    Note that ``is_expired`` is a property, not a column, so the expiry test happens in
    Python -- the queryset only narrows to verified records that still hold evidence.

    A verify_type whose ``*_DAYS`` setting is ``0`` never expires (height, by default), so
    those records are never reached by this sweep. That is the validity setting working as
    intended -- an adult's height does not change, so the verification stays good.

    It is not a reason to keep the photograph forever, which is what used to happen.
    ``purge_aged_verification_media`` below is the backstop: how long a verification stays
    valid and how long we keep the body image that evidenced it are separate questions.

    Returns:
        Counts of records ``considered``, ``purged`` and ``failed``.

    """
    candidates = RaceReadyRecord.objects.filter(status=RaceReadyRecord.Status.VERIFIED).filter(_has_media())
    expired = [record for record in candidates if record.is_expired]
    result = _strip_media(expired, reason="expired")
    logfire.info("Purged expired verification media", **result)
    return result


def purge_aged_verification_media() -> dict[str, int]:
    """Strip evidence we have held longer than ``VERIFICATION_MEDIA_MAX_DAYS``.

    The backstop for verification types that never expire. Height is the case that motivated
    it: the verification is valid forever and the photograph was therefore kept forever, so a
    body image uploaded in 2023 was still in storage with nothing scheduled to remove it.

    Measured from ``date_created`` -- when we took possession of the file -- rather than
    ``record_date``, which the expiry logic uses. ``record_date`` is the date the evidence
    depicts and a rider may enter one well in the past, so measuring from it would delete a
    backdated record's media the day it was uploaded. The question here is how long we have
    held the file, not how old the measurement is.

    Only verified records are swept. A pending record's media is the evidence a reviewer has
    yet to look at, and rejected media has its own shorter sweep.

    Returns:
        Counts of records ``considered``, ``purged`` and ``failed``. All zero when the
        setting is ``0``, which keeps media indefinitely.

    """
    max_days = config.VERIFICATION_MEDIA_MAX_DAYS
    if not max_days or max_days <= 0:
        logfire.info("Verification media retention disabled, skipping aged sweep")
        return {"considered": 0, "purged": 0, "failed": 0}

    cutoff = timezone.now() - timedelta(days=max_days)
    aged = list(
        RaceReadyRecord.objects.filter(
            status=RaceReadyRecord.Status.VERIFIED,
            date_created__lt=cutoff,
        ).filter(_has_media())
    )
    result = _strip_media(aged, reason="aged out")
    logfire.info("Purged aged verification media", max_days=max_days, **result)
    return result


def purge_rejected_verification_media(older_than_days: int = 30) -> dict[str, int]:
    """Strip evidence from records rejected longer ago than ``older_than_days``.

    Args:
        older_than_days: How long a rejected record keeps its evidence, so a rider has a
            window to query the decision before it is thrown away.

    Returns:
        Counts of records ``considered``, ``purged`` and ``failed``.

    """
    cutoff = timezone.now() - timedelta(days=older_than_days)
    stale = list(
        RaceReadyRecord.objects.filter(
            status=RaceReadyRecord.Status.REJECTED,
            reviewed_date__lt=cutoff,
        ).filter(_has_media())
    )
    result = _strip_media(stale, reason="rejected")
    logfire.info("Purged rejected verification media", older_than_days=older_than_days, **result)
    return result


# How long a minted verification-media URL stays valid. Deliberately short: the URL is a
# bearer token, so this is the window in which a leaked one is still worth something. It is
# not measured from page render but from the moment the reviewer asks for the file, so it
# only has to outlive a single fetch -- plus enough slack for a browser to seek within a
# video, which re-requests the resolved URL rather than coming back through the view.
VERIFICATION_MEDIA_URL_TTL_SECONDS = 300

# Video needs a longer window than a photo, and not for convenience. A browser resolves a
# <source> once and then issues range requests against the *resolved* URL for the rest of
# playback -- it does not come back through the view. So the link has to outlive the viewing
# session, not just the first fetch. At 300s a reviewer who paused to read the notes and then
# scrubbed backwards would hit a silent stall with no error to explain it.
VERIFICATION_VIDEO_URL_TTL_SECONDS = 1800


# Grace period before a rejected record's evidence is purged. Mirrors
# REJECTED_MEDIA_GRACE_DAYS in apps/team/views.py, which is what the admin button uses.
REJECTED_MEDIA_GRACE_DAYS = 30


@dataclass(frozen=True)
class MediaRetentionRule:
    """One rule that will eventually remove a record's evidence.

    Attributes:
        label: Short name for the rule, as a rider should read it.
        due: The date the rule bites, or None when it cannot be dated.
        automatic: Whether anything actually enforces it on a schedule. A rule that only
            runs when an admin presses a button is not a promise, and saying so is the
            difference between a retention notice and a retention aspiration.
        detail: Why this rule applies to this record.

    """

    label: str
    due: date | None
    automatic: bool
    detail: str


def media_retention_rules(record: RaceReadyRecord) -> list[MediaRetentionRule]:
    """Every rule that will remove ``record``'s evidence, with the date each one bites.

    Derived from the sweeps themselves rather than restated, because a retention notice that
    disagrees with the code is worse than none -- it is a claim about someone's data that
    cannot be honoured. Three sweeps exist and they do not cover the same records:

    * ``purge_expired_verification_media`` -- VERIFIED only, once the verification lapses.
      A type whose ``*_DAYS`` is 0 never expires, so this never reaches it.
    * ``purge_aged_verification_media`` -- VERIFIED only, ``VERIFICATION_MEDIA_MAX_DAYS``
      after we took possession. This is the backstop for the case above.
    * ``purge_rejected_verification_media`` -- REJECTED only, and **not scheduled**: it runs
      from an admin button. Reported with ``automatic=False`` rather than dressed up as a date.

    A pending record is covered by none of them, which is worth saying plainly instead of
    leaving a blank space where a date should be.

    Args:
        record: The verification record.

    Returns:
        The applicable rules, soonest dated first, undated last.

    """
    rules: list[MediaRetentionRule] = []

    if record.status == RaceReadyRecord.Status.VERIFIED:
        expires = record.expires_date
        if expires is not None:
            rules.append(
                MediaRetentionRule(
                    label="When this verification expires",
                    due=expires,
                    automatic=True,
                    detail=(
                        f"{record.get_verify_type_display()} verifications stay valid for "
                        f"{record.validity_days} days."
                    ),
                )
            )
        max_days = config.VERIFICATION_MEDIA_MAX_DAYS
        if max_days and max_days > 0:
            rules.append(
                MediaRetentionRule(
                    label="Retention limit",
                    due=(record.date_created + timedelta(days=max_days)).date(),
                    automatic=True,
                    detail=(
                        f"We do not keep evidence longer than {max_days} days, even while the "
                        "verification is still valid."
                    ),
                )
            )
    elif record.status == RaceReadyRecord.Status.REJECTED and record.reviewed_date:
        rules.append(
            MediaRetentionRule(
                label="After rejection",
                due=(record.reviewed_date + timedelta(days=REJECTED_MEDIA_GRACE_DAYS)).date(),
                automatic=False,
                detail=(
                    f"Rejected evidence is removed {REJECTED_MEDIA_GRACE_DAYS} days after review, "
                    "but this runs when an administrator triggers it rather than on a schedule."
                ),
            )
        )
    elif record.is_pending:
        rules.append(
            MediaRetentionRule(
                label="While awaiting review",
                due=None,
                automatic=False,
                detail="Evidence is kept until the record is reviewed. No removal is scheduled before then.",
            )
        )

    return sorted(rules, key=lambda rule: (rule.due is None, rule.due))


def can_view_verification_media(user: User, record: RaceReadyRecord) -> bool:
    """Report whether ``user`` may see ``record``'s uploaded evidence.

    This is the single definition of that rule. The review page uses it to decide whether to
    render the media block, and the media view uses it to decide whether to hand out a URL --
    they must not be able to disagree, or the second becomes a way around the first.

    Three conditions, all required:

    1. The viewer reviews verifications at all (or is a superuser).
    2. The same-gender restriction, when the rider asked for one, is satisfied. Superusers
       bypass this, matching the review page.
    3. The record is still pending, or the viewer is on the performance verification team.
       Once a record is decided, the evidence stops being visible to ordinary reviewers --
       it was uploaded to support a decision that has now been made.

    Args:
        user: The person asking to see the media.
        record: The verification record holding it.

    Returns:
        True if the media may be shown to this user.

    """
    from apps.accounts.models import Permissions

    if not (user.can_approve_verification or user.is_superuser):
        return False

    if record.same_gender and not user.is_superuser and record.user.gender != user.gender:
        return False

    return record.is_pending or user.has_permission(Permissions.PERFORMANCE_VERIFICATION_TEAM)


_REVIEWER_POOL_CACHE_KEY = "verification:reviewer_pool_ids"
_REVIEWER_POOL_TTL_SECONDS = 300


def _reviewer_pool_ids() -> list[int]:
    """Primary keys of everyone who can approve verifications at all.

    Permissions resolve through Discord roles and per-user overrides in Python, so this
    cannot be a queryset filter. The candidate set is narrowed to users who could plausibly
    hold a permission before the real check runs, and the result is cached briefly -- the
    membership changes when Discord roles change, which is not a per-request event.

    Only ids are cached. The gate itself is re-run against live User objects by
    :func:`media_access_summary`, so the rule has exactly one definition.

    Returns:
        User primary keys, unordered.

    """
    cached = cache.get(_REVIEWER_POOL_CACHE_KEY)
    if cached is not None:
        return cached

    candidates = User.objects.filter(
        Q(is_superuser=True) | ~Q(discord_roles={}) | ~Q(permission_overrides={})
    ).only("id", "is_superuser", "discord_roles", "permission_overrides", "roles")
    ids = [user.pk for user in candidates if user.is_superuser or user.can_approve_verification]
    cache.set(_REVIEWER_POOL_CACHE_KEY, ids, _REVIEWER_POOL_TTL_SECONDS)
    return ids


def media_access_summary(record: RaceReadyRecord) -> dict:
    """How many people can currently open ``record``'s evidence, and under what restriction.

    Counted by running :func:`can_view_verification_media` against the live reviewer pool, so
    this cannot drift from what the endpoint actually permits.

    Deliberately a count and a category rather than names. Article 15 asks for "the recipients
    or categories of recipient", and naming the individuals who reviewed a body photograph
    exposes them to whoever disputes the outcome. The count is the part that tells a rider
    something they can act on.

    Args:
        record: The verification record.

    Returns:
        ``count`` of people who can see it now, ``same_gender_only`` reflecting the rider's
        own request, and ``restricted_to_team`` when the record is decided and only the
        performance verification team retains access.

    """
    reviewers = User.objects.filter(pk__in=_reviewer_pool_ids())
    eligible = [user for user in reviewers if can_view_verification_media(user, record)]
    return {
        "count": len(eligible),
        "same_gender_only": bool(record.same_gender),
        "restricted_to_team": not record.is_pending,
    }


def viewer_pseudonym(owner_id: int, viewer_id: int) -> str:
    """Build a stable, non-reversible tag for a viewer, shown to the record's owner.

    The same reviewer reads the same across all of one rider's records, so a rider can see
    that one person opened three submissions rather than three people opening one each. The
    owner is part of the key, so two riders comparing notes cannot line their tags up and
    rebuild the reviewer roster between them.

    Not a privacy control on its own -- with a handful of reviewers the set is small enough to
    guess from -- which is why the access summary gives a count rather than names.

    Args:
        owner_id: The rider whose record it is.
        viewer_id: The person who viewed it.

    Returns:
        Six hex characters.

    """
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        f"verification-viewer:{owner_id}:{viewer_id}".encode(),
        hashlib.sha256,
    )
    return digest.hexdigest()[:6]


def record_view_trail(record: RaceReadyRecord) -> list[dict]:
    """Return the access trail for ``record``, as its owner should see it.

    ``RecordView`` has existed for a while but was shown only to reviewers, which left the
    data subject as the one person unable to see who had looked at their own photographs.

    Only views that actually displayed the evidence are listed. Opening the page without
    passing the media gate is a different event, and reporting it as "viewed" would overstate
    what the person saw.

    Args:
        record: The verification record.

    Returns:
        One entry per viewer, most recent first, each with a pseudonym and counts.

    """
    views = RecordView.objects.filter(record=record, media_view_count__gt=0).order_by("-last_viewed_at")
    return [
        {
            "tag": viewer_pseudonym(record.user_id, view.user_id),
            "first_viewed_at": view.first_viewed_at,
            "last_viewed_at": view.last_viewed_at,
            "media_view_count": view.media_view_count,
        }
        for view in views
    ]


def verification_media_url(record: RaceReadyRecord) -> str:
    """Mint a short-lived URL for ``record``'s media file.

    Callers must have already cleared :func:`can_view_verification_media`.

    Args:
        record: The record whose ``media_file`` should be addressed.

    Returns:
        A URL valid for :data:`VERIFICATION_MEDIA_URL_TTL_SECONDS`, or
        :data:`VERIFICATION_VIDEO_URL_TTL_SECONDS` for video, on backends that sign URLs.
        Local filesystem storage takes no expiry and returns its plain path.

    """
    media = record.media_file
    url = media.storage.url
    ttl = VERIFICATION_VIDEO_URL_TTL_SECONDS if record.media_type == "video" else VERIFICATION_MEDIA_URL_TTL_SECONDS

    # Ask the backend whether it takes an expiry rather than calling and catching TypeError.
    # A blanket except would also swallow a TypeError raised *inside* a signing backend, and
    # the fallback below returns a URL on the much longer storage-wide clock -- turning a bug
    # into a silent downgrade of this control. Better to let such an error surface.
    if "expire" in inspect.signature(url).parameters:
        return url(media.name, expire=ttl)

    # FileSystemStorage.url() takes no expiry; local dev serves straight off disk.
    return media.url


def log_record_view(record: RaceReadyRecord, user: User, *, media_shown: bool) -> None:
    """Record that ``user`` opened ``record``'s detail page.

    Keeps one row per (record, user) pair and bumps its counters, so opening a record
    repeatedly updates the same row instead of appending history. Counter updates run as
    an atomic UPDATE so concurrent views can't lose increments. Failures are logged and
    swallowed — an audit write must never break the review page.

    Args:
        record: The verification record being viewed.
        user: The viewer.
        media_shown: Whether the page actually displayed the record's media to this user.

    """
    try:
        view, _created = RecordView.objects.get_or_create(record=record, user=user)
        RecordView.objects.filter(pk=view.pk).update(
            view_count=F("view_count") + 1,
            media_view_count=F("media_view_count") + (1 if media_shown else 0),
            last_viewed_at=timezone.now(),
        )
        logfire.info(
            "Verification record viewed",
            record_id=record.pk,
            record_owner_id=record.user_id,
            viewer_id=user.pk,
            media_shown=media_shown,
        )
    except Exception as e:  # never let the audit write break the review page
        logfire.error(
            "Failed to log verification record view",
            record_id=record.pk,
            viewer_id=user.pk,
            error=str(e),
        )


def _covers_longer(candidate: RaceReadyRecord, current: RaceReadyRecord) -> bool:
    """Whether ``candidate`` provides coverage further into the future than ``current``.

    A never-expiring record (``days_remaining is None``) outranks any finite one;
    between two finite records the greater ``days_remaining`` wins.

    Args:
        candidate: The record being considered.
        current: The current best record for the same type.

    Returns:
        True if ``candidate`` should replace ``current`` as the covering record.

    """
    current_days = current.days_remaining
    if current_days is None:
        return False  # current never expires — nothing beats it
    candidate_days = candidate.days_remaining
    if candidate_days is None:
        return True  # candidate never expires
    return candidate_days > current_days


def covering_records_by_type(records) -> dict[str, RaceReadyRecord]:
    """Collapse verified records to the single coverage-defining record per verify_type.

    ``User.calculate_race_ready`` treats a verification type as satisfied when *any*
    non-expired verified record of that type exists, so a type's real coverage is set
    by its longest-lived record, not by each record individually. Reducing to that one
    record per type is what lets a freshly-renewed verification supersede the record it
    replaces: the expiring old record must not drive "expiring soon" warnings (DM banner
    or web banner) while a newer record of the same type still covers it.

    Args:
        records: Iterable of **verified** RaceReadyRecord instances (mixed types allowed).

    Returns:
        Mapping of ``verify_type`` -> the longest-lived record of that type. A type whose
        covering record never expires has ``days_remaining is None``; a type whose every
        record has already expired keeps its (negative-``days_remaining``) record.

    """
    covering: dict[str, RaceReadyRecord] = {}
    for record in records:
        current = covering.get(record.verify_type)
        if current is None or _covers_longer(record, current):
            covering[record.verify_type] = record
    return covering


@dataclass
class UnifiedRider:
    """Unified rider data from all sources."""

    zwid: int

    # User data
    has_account: bool = False
    user_id: int | None = None
    username: str = ""
    discord_id: str = ""
    discord_username: str = ""
    discord_avatar_url: str = ""
    zwid_verified: bool = False
    user_gender: str = ""

    # ZwiftPower data
    in_zwiftpower: bool = False
    zp_name: str = ""
    zp_div: int = 0
    zp_divw: int = 0
    zp_date_left: datetime | None = None
    zp_rank: Decimal | None = None
    zp_ftp: int | None = None
    zp_weight: Decimal | None = None

    # Zwift Racing data
    in_zwiftracing: bool = False
    zr_name: str = ""
    zr_category: str = ""
    zr_date_left: datetime | None = None
    zr_rating: Decimal | None = None
    zr_phenotype: str = ""

    # ZwiftPower Results (aggregated)
    has_results: bool = False
    result_count: int = 0

    # Guild data
    guild_joined_at: datetime | None = None

    # Race Ready status
    is_race_ready: bool = False
    is_extra_verified: bool = False
    # Class variable for div mapping
    DIV_TO_CATEGORY: ClassVar[dict[int, str]] = ZP_DIV_TO_CATEGORY

    @property
    def display_name(self) -> str:
        """Best available name."""
        return self.zp_name or self.zr_name or self.username or f"Rider {self.zwid}"

    @property
    def zp_category(self) -> str:
        """ZwiftPower category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_div, "")

    @property
    def zp_category_w(self) -> str:
        """ZwiftPower women's category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_divw, "")

    @property
    def gender(self) -> str:
        """Gender with fallback logic.

        Priority: user profile gender > ZP divw (if > 0 = F, else M).

        Returns:
            'M' for male, 'F' for female, or '' if unknown.

        """
        # First check user profile gender (normalize from 'male'/'female' to 'M'/'F')
        if self.user_gender:
            if self.user_gender == "male":
                return "M"
            if self.user_gender == "female":
                return "F"
            return ""  # 'other' or unknown
        # Fall back to ZwiftPower: if divw > 0, they are female
        if self.in_zwiftpower:
            return "F" if self.zp_divw > 0 else "M"
        return ""

    @property
    def is_active_member(self) -> bool:
        """Check if rider is active in at least one source."""
        return (self.in_zwiftpower and not self.zp_date_left) or (self.in_zwiftracing and not self.zr_date_left)

    @property
    def zp_active(self) -> bool:
        """Check if rider is active in ZwiftPower."""
        return self.in_zwiftpower and not self.zp_date_left

    @property
    def zr_active(self) -> bool:
        """Check if rider is active in Zwift Racing."""
        return self.in_zwiftracing and not self.zr_date_left

    @property
    def membership_status(self) -> str:
        """Detailed membership status.

        Returns:
            One of: 'both', 'zp_only', 'zr_only', 'left', 'none'

        """
        if self.zp_active and self.zr_active:
            return "both"
        if self.zp_active:
            return "zp_only"
        if self.zr_active:
            return "zr_only"
        if self.in_zwiftpower or self.in_zwiftracing:
            return "left"
        return "none"

    @property
    def wkg(self) -> Decimal | None:
        """Calculate watts per kilogram from FTP and weight.

        Returns:
            FTP divided by weight, rounded to 2 decimal places, or None if either is missing.

        """
        if self.zp_ftp is not None and self.zp_weight is not None and self.zp_weight > 0:
            return round(Decimal(self.zp_ftp) / self.zp_weight, 2)
        return None

    @property
    def member_since(self) -> str:
        """Human-readable duration since guild join.

        Returns:
            String like '2y 3m' or '--' if no join date.

        """
        if not self.guild_joined_at:
            return "--"
        from django.utils import timezone

        now = timezone.now()
        delta = now - self.guild_joined_at
        total_months = delta.days // 30
        years = total_months // 12
        months = total_months % 12
        if years > 0:
            return f"{years}y {months}m"
        return f"{months}m"

    @property
    def member_since_sort_key(self) -> int:
        """Sortable value for member since (days since join, 0 if unknown)."""
        if not self.guild_joined_at:
            return 0
        from django.utils import timezone

        return (timezone.now() - self.guild_joined_at).days


def get_unified_team_roster() -> list[UnifiedRider]:
    """Get unified team roster from all data sources.

    Returns:
        List of UnifiedRider objects sorted by display name.

    """
    # Query each source with .values() for efficiency
    users = User.objects.filter(zwid__isnull=False).values(
        "id", "zwid", "username", "discord_username", "discord_id", "discord_avatar", "zwid_verified", "gender",
        "zwid_verification_method",
        "is_race_ready", "is_extra_verified",
    )
    zp_riders = ZPTeamRiders.objects.all().values(
        "zwid", "name", "div", "divw", "date_left", "rank", "ftp", "weight"
    )
    zr_riders = ZRRider.objects.all().values(
        "zwid", "name", "race_current_category", "date_left", "race_current_rating", "phenotype_value",
    )

    # Get result counts per rider
    result_counts = ZPRiderResults.objects.values("zwid").annotate(count=Count("id"))

    # Get guild member join dates (keyed by user_id)
    guild_members = GuildMember.objects.filter(user__isnull=False).values("user_id", "joined_at")
    guild_joined_by_user_id: dict[int, datetime | None] = {gm["user_id"]: gm["joined_at"] for gm in guild_members}

    # Build lookup dicts
    user_by_zwid: dict[int, dict] = {u["zwid"]: u for u in users}
    zp_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zp_riders}
    zr_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zr_riders}
    results_by_zwid: dict[int, int] = {r["zwid"]: r["count"] for r in result_counts}

    # Collect all unique zwids
    zwid_set: set[int] = set()
    zwid_set.update(user_by_zwid.keys())
    zwid_set.update(zp_by_zwid.keys())
    zwid_set.update(zr_by_zwid.keys())

    # Build unified list
    unified: list[UnifiedRider] = []

    for zwid in zwid_set:
        rider = UnifiedRider(zwid=zwid)

        # User data
        if zwid in user_by_zwid:
            u = user_by_zwid[zwid]
            rider.has_account = True
            rider.user_id = u["id"]
            rider.username = u["username"]
            rider.discord_id = u["discord_id"] or ""
            rider.discord_username = u["discord_username"] or ""
            rider.zwid_verified = verification_accepted(u)
            rider.user_gender = u["gender"] or ""
            rider.is_race_ready = u["is_race_ready"]
            rider.is_extra_verified = u["is_extra_verified"]

            if u["discord_id"] and u["discord_avatar"]:
                rider.discord_avatar_url = (
                    f"https://cdn.discordapp.com/avatars/{u['discord_id']}/{u['discord_avatar']}.png"
                )

            rider.guild_joined_at = guild_joined_by_user_id.get(u["id"])

        # ZwiftPower data
        if zwid in zp_by_zwid:
            zp = zp_by_zwid[zwid]
            rider.in_zwiftpower = True
            rider.zp_name = zp["name"]
            rider.zp_div = zp["div"]
            rider.zp_divw = zp["divw"]
            rider.zp_date_left = zp["date_left"]
            rider.zp_rank = zp["rank"]
            rider.zp_ftp = zp["ftp"]
            rider.zp_weight = zp["weight"]

        # Zwift Racing data
        if zwid in zr_by_zwid:
            zr = zr_by_zwid[zwid]
            rider.in_zwiftracing = True
            rider.zr_name = zr["name"]
            rider.zr_category = zr["race_current_category"] or ""
            rider.zr_date_left = zr["date_left"]
            rider.zr_rating = zr["race_current_rating"]
            rider.zr_phenotype = zr["phenotype_value"] or ""

        # Results data
        if zwid in results_by_zwid:
            rider.has_results = True
            rider.result_count = results_by_zwid[zwid]

        unified.append(rider)

    # Sort by display name
    sorted_roster = sorted(unified, key=lambda r: r.display_name.lower())

    logfire.debug(
        "Unified team roster loaded",
        total_riders=len(sorted_roster),
        users_with_zwid=len(user_by_zwid),
        zp_riders=len(zp_by_zwid),
        zr_riders=len(zr_by_zwid),
    )

    return sorted_roster


def get_unified_rider(zwid: int) -> UnifiedRider | None:
    """Get unified data for a single rider.

    Args:
        zwid: The Zwift ID to look up.

    Returns:
        UnifiedRider or None if not found in any source.

    """
    roster = get_unified_team_roster()
    for rider in roster:
        if rider.zwid == zwid:
            return rider
    return None


@dataclass
class PerformanceRider:
    """Performance review data combining verification records with ZwiftPower results."""

    zwid: int
    display_name: str = ""
    zp_div: int = 0
    zp_divw: int = 0
    gender: str = ""
    has_account: bool = False
    user_id: int | None = None
    discord_id: str = ""
    discord_avatar_url: str = ""
    is_race_ready: bool = False
    is_extra_verified: bool = False
    in_zwiftpower: bool = False
    in_zwiftracing: bool = False
    zr_category: str = ""
    zr_rating: Decimal | None = None
    zr_phenotype: str = ""
    zr_age: str = ""

    # Verification data - weight light
    weight_light_date: datetime | None = None
    weight_light_value: Decimal | None = None

    # Verification data - weight full
    weight_full_date: datetime | None = None
    weight_full_value: Decimal | None = None

    # Verification data - height
    height_date: datetime | None = None
    height_value: int | None = None

    # ZwiftPower result data
    zp_result_date: datetime | None = None
    zp_result_weight: Decimal | None = None
    zp_height_date: datetime | None = None
    zp_height_value: int | None = None

    # FTP data (from ZPTeamRiders and history)
    ftp_current: int | None = None
    ftp_min: int | None = None
    ftp_max: int | None = None

    # ZwiftPower current weight (for WKG calculation)
    zp_weight: Decimal | None = None

    # Class variable for div mapping
    DIV_TO_CATEGORY: ClassVar[dict[int, str]] = ZP_DIV_TO_CATEGORY

    @property
    def zp_category(self) -> str:
        """ZwiftPower category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_div, "")

    @property
    def zp_category_w(self) -> str:
        """ZwiftPower women's category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_divw, "")

    @property
    def latest_verification_weight(self) -> Decimal | None:
        """The most recent verification weight (full or light)."""
        if self.weight_full_date and self.weight_light_date:
            if self.weight_full_date >= self.weight_light_date:
                return self.weight_full_value
            return self.weight_light_value
        if self.weight_full_value is not None:
            return self.weight_full_value
        return self.weight_light_value

    @property
    def weight_diff(self) -> Decimal | None:
        """Difference between latest verification weight and ZP result weight.

        Returns:
            Positive if ZP weight is higher than verification, negative if lower.
            None if either value is missing.

        """
        verification_weight = self.latest_verification_weight
        if verification_weight is None or self.zp_result_weight is None:
            return None
        return verification_weight - self.zp_result_weight

    @property
    def weight_diff_abs(self) -> Decimal | None:
        """Absolute value of weight difference for sorting."""
        diff = self.weight_diff
        return abs(diff) if diff is not None else None

    @property
    def has_weight_concern(self) -> bool:
        """True if weight decreased by more than 2kg (diff < -2)."""
        diff = self.weight_diff
        return diff is not None and diff < -2

    @property
    def has_severe_weight_concern(self) -> bool:
        """True if weight decreased by more than 5kg (diff < -5)."""
        diff = self.weight_diff
        return diff is not None and diff < -5

    @property
    def height_diff(self) -> int | None:
        """Difference between verification height and ZP result height in cm.

        Returns:
            Positive if verification is taller, negative if shorter.
            None if either value is missing.

        """
        if self.height_value is None or self.zp_height_value is None:
            return None
        return self.height_value - self.zp_height_value

    @property
    def wkg(self) -> Decimal | None:
        """Calculate watts per kilogram from FTP and weight.

        Returns:
            FTP divided by weight, rounded to 2 decimal places, or None if either is missing.

        """
        if self.ftp_current is not None and self.zp_weight is not None and self.zp_weight > 0:
            return round(Decimal(self.ftp_current) / self.zp_weight, 2)
        return None


def get_performance_review_data() -> list[PerformanceRider]:
    """Get performance review data for all riders (outer join of ZP, ZR, Users).

    Includes all riders from ZwiftPower, Zwift Racing, and Users,
    excluding those who have left (zp_date_left is set).

    Returns:
        List of PerformanceRider objects sorted by display name.

    """
    # Get unified roster for basic rider info (outer join of all sources)
    roster = get_unified_team_roster()

    # Filter out riders who have left ZwiftPower
    roster = [r for r in roster if not r.zp_date_left]

    # Build lookup of rider info by zwid
    rider_info: dict[int, dict] = {}
    for r in roster:
        rider_info[r.zwid] = {
            "display_name": r.display_name,
            "zp_div": r.zp_div,
            "zp_divw": r.zp_divw,
            "gender": r.gender,
            "has_account": r.has_account,
            "user_id": r.user_id,
            "discord_id": r.discord_id,
            "discord_avatar_url": r.discord_avatar_url,
            "is_race_ready": r.is_race_ready,
            "is_extra_verified": r.is_extra_verified,
            "in_zwiftpower": r.in_zwiftpower,
            "in_zwiftracing": r.in_zwiftracing,
            "zr_category": r.zr_category,
            "zr_rating": r.zr_rating,
            "zr_phenotype": r.zr_phenotype,
        }

    if not rider_info:
        logfire.debug("Performance review data: no active riders found")
        return []

    # Get most recent verified records per user per type using subquery
    # First, get the latest verified record ID for each user/type combination
    latest_verified_subquery = RaceReadyRecord.objects.filter(
        user__zwid=OuterRef("user__zwid"),
        verify_type=OuterRef("verify_type"),
        status=RaceReadyRecord.Status.VERIFIED,
    ).order_by("-reviewed_date").values("pk")[:1]

    verified_records = RaceReadyRecord.objects.filter(
        status=RaceReadyRecord.Status.VERIFIED,
        user__zwid__in=rider_info.keys(),
        pk__in=Subquery(latest_verified_subquery),
    ).select_related("user").values(
        "user__zwid", "verify_type", "weight", "height", "reviewed_date"
    )

    # Build lookup of verification data by zwid
    verification_by_zwid: dict[int, dict] = {}
    for record in verified_records:
        zwid = record["user__zwid"]
        if zwid not in verification_by_zwid:
            verification_by_zwid[zwid] = {}

        verify_type = record["verify_type"]
        if verify_type == "weight_light":
            verification_by_zwid[zwid]["weight_light_date"] = record["reviewed_date"]
            verification_by_zwid[zwid]["weight_light_value"] = record["weight"]
        elif verify_type == "weight_full":
            verification_by_zwid[zwid]["weight_full_date"] = record["reviewed_date"]
            verification_by_zwid[zwid]["weight_full_value"] = record["weight"]
        elif verify_type == "height":
            verification_by_zwid[zwid]["height_date"] = record["reviewed_date"]
            verification_by_zwid[zwid]["height_value"] = record["height"]

    # Get most recent ZP result with weight/height for each rider (batch query)
    # Fetch all results at once instead of one query per rider
    zp_data_by_zwid: dict[int, dict] = {}
    all_results = (
        ZPRiderResults.objects.filter(zwid__in=rider_info.keys())
        .exclude(weight__isnull=True, height__isnull=True)
        .select_related("event")
        .order_by("zwid", "-event__event_date")
        .values("zwid", "event__event_date", "weight", "height")
    )

    # Group results by zwid and find most recent weight/height
    for result in all_results:
        zwid = result["zwid"]
        event_date = result["event__event_date"]
        weight = result["weight"]
        height = result["height"]

        if zwid not in zp_data_by_zwid:
            zp_data_by_zwid[zwid] = {}

        # Store most recent weight (first one we see due to ordering)
        if weight is not None and "zp_result_date" not in zp_data_by_zwid[zwid]:
            zp_data_by_zwid[zwid]["zp_result_date"] = event_date
            zp_data_by_zwid[zwid]["zp_result_weight"] = weight

        # Store most recent height (first one we see due to ordering)
        if height is not None and "zp_height_date" not in zp_data_by_zwid[zwid]:
            zp_data_by_zwid[zwid]["zp_height_date"] = event_date
            zp_data_by_zwid[zwid]["zp_height_value"] = height

    # Get current FTP and weight from ZPTeamRiders
    zp_riders = ZPTeamRiders.objects.filter(zwid__in=rider_info.keys()).values("zwid", "ftp", "weight")
    ftp_current_by_zwid: dict[int, int | None] = {r["zwid"]: r["ftp"] for r in zp_riders}
    weight_by_zwid: dict[int, Decimal | None] = {r["zwid"]: r["weight"] for r in zp_riders}

    # Get FTP history (min/max) for each rider (batch query)
    # Query historical records directly with aggregation instead of one query per rider
    ftp_stats_by_zwid: dict[int, dict] = {}
    ftp_agg = (
        ZPTeamRiders.history.filter(zwid__in=rider_info.keys())
        .exclude(ftp__isnull=True)
        .values("zwid")
        .annotate(ftp_min=Min("ftp"), ftp_max=Max("ftp"))
    )
    for stat in ftp_agg:
        ftp_stats_by_zwid[stat["zwid"]] = {
            "ftp_min": stat["ftp_min"],
            "ftp_max": stat["ftp_max"],
        }

    # Build PerformanceRider objects
    performance_riders: list[PerformanceRider] = []
    for zwid, info in rider_info.items():
        rider = PerformanceRider(
            zwid=zwid,
            display_name=info["display_name"],
            zp_div=info["zp_div"],
            zp_divw=info["zp_divw"],
            gender=info["gender"],
            has_account=info["has_account"],
            user_id=info["user_id"],
            discord_id=info["discord_id"],
            discord_avatar_url=info["discord_avatar_url"],
            is_race_ready=info["is_race_ready"],
            is_extra_verified=info["is_extra_verified"],
            in_zwiftpower=info["in_zwiftpower"],
            in_zwiftracing=info["in_zwiftracing"],
            zr_category=info["zr_category"],
            zr_rating=info["zr_rating"],
            zr_phenotype=info["zr_phenotype"],
        )

        # Add verification data
        if zwid in verification_by_zwid:
            v = verification_by_zwid[zwid]
            rider.weight_light_date = v.get("weight_light_date")
            rider.weight_light_value = v.get("weight_light_value")
            rider.weight_full_date = v.get("weight_full_date")
            rider.weight_full_value = v.get("weight_full_value")
            rider.height_date = v.get("height_date")
            rider.height_value = v.get("height_value")

        # Add ZP data
        if zwid in zp_data_by_zwid:
            zp = zp_data_by_zwid[zwid]
            rider.zp_result_date = zp.get("zp_result_date")
            rider.zp_result_weight = zp.get("zp_result_weight")
            rider.zp_height_date = zp.get("zp_height_date")
            rider.zp_height_value = zp.get("zp_height_value")

        # Add FTP and weight data
        rider.ftp_current = ftp_current_by_zwid.get(zwid)
        rider.zp_weight = weight_by_zwid.get(zwid)
        if zwid in ftp_stats_by_zwid:
            ftp_stats = ftp_stats_by_zwid[zwid]
            rider.ftp_min = ftp_stats.get("ftp_min")
            rider.ftp_max = ftp_stats.get("ftp_max")

        performance_riders.append(rider)

    # Sort by display name
    sorted_riders = sorted(performance_riders, key=lambda r: r.display_name.lower())

    logfire.debug(
        "Performance review data loaded",
        total_riders=len(sorted_riders),
        riders_with_verifications=len(verification_by_zwid),
        riders_with_results=len(zp_data_by_zwid),
    )

    return sorted_riders


@dataclass
class MembershipReviewRider:
    """Member data for membership review view."""

    zwid: int = 0
    user_id: int | None = None

    # User data
    full_name: str = ""
    discord_id: str = ""
    discord_nickname: str = ""

    # Guild data
    guild_nickname: str = ""
    in_guild: bool = False
    guild_joined_at: datetime | None = None

    # ZP/ZR names
    zp_name: str = ""
    zr_name: str = ""

    # Status
    gender: str = ""
    has_account: bool = True
    zwid_verified: bool = False

    # Category
    zp_div: int = 0

    # ZP/ZR membership status
    in_zwiftpower: bool = False
    in_zwiftracing: bool = False
    zp_date_left: datetime | None = None
    zr_date_left: datetime | None = None

    # Results data
    result_count: int = 0
    last_result_date: datetime | None = None

    # Member profile fields
    birth_year: int | None = None
    city: str = ""
    country: str = ""
    country_name: str = ""  # Full name for display
    timezone: str = ""

    # Equipment
    trainer: str = ""
    powermeter: str = ""
    dual_recording: bool | None = None
    heartrate_monitor: str = ""
    has_jersey: bool = False

    # Emergency contact
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""

    # Class variable for div mapping
    DIV_TO_CATEGORY: ClassVar[dict[int, str]] = ZP_DIV_TO_CATEGORY

    @property
    def zp_category(self) -> str:
        """ZwiftPower category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_div, "")

    @property
    def days_since_result(self) -> int | None:
        """Days since last result, or None if no results."""
        if not self.last_result_date:
            return None
        from django.utils import timezone
        delta = timezone.now() - self.last_result_date
        return delta.days

    @property
    def days_since_zp_left(self) -> int | None:
        """Days since rider left ZwiftPower, or None if still active."""
        if not self.zp_date_left:
            return None
        from django.utils import timezone
        delta = timezone.now() - self.zp_date_left
        return delta.days

    @property
    def guild_membership_duration(self) -> str:
        """Formatted guild membership duration as 'Xy Xd'."""
        if not self.guild_joined_at:
            return ""
        from django.utils import timezone
        delta = timezone.now() - self.guild_joined_at
        total_days = delta.days
        years = total_days // 365
        days = total_days % 365
        if years > 0:
            return f"{years}y {days}d"
        return f"{days}d"

    @property
    def guild_membership_days(self) -> int | None:
        """Total days of guild membership, or None if unknown."""
        if not self.guild_joined_at:
            return None
        from django.utils import timezone
        return (timezone.now() - self.guild_joined_at).days

    @property
    def discord_profile_url(self) -> str:
        """Discord profile URL for this user."""
        if self.discord_id:
            return f"https://discord.com/users/{self.discord_id}"
        return ""

    @property
    def zp_active(self) -> bool:
        """Check if rider is active in ZwiftPower."""
        return self.in_zwiftpower and not self.zp_date_left

    @property
    def zr_active(self) -> bool:
        """Check if rider is active in Zwift Racing."""
        return self.in_zwiftracing and not self.zr_date_left

    @property
    def is_active_member(self) -> bool:
        """Check if rider is active in at least one source."""
        return self.zp_active or self.zr_active

    @property
    def membership_status(self) -> str:
        """Detailed membership status.

        Returns:
            One of: 'both', 'zp_only', 'zr_only', 'left', 'none'

        """
        if self.zp_active and self.zr_active:
            return "both"
        if self.zp_active:
            return "zp_only"
        if self.zr_active:
            return "zr_only"
        if self.in_zwiftpower or self.in_zwiftracing:
            return "left"
        return "none"


def get_membership_review_data() -> list[MembershipReviewRider]:
    """Get membership review data with outer join across all sources.

    Performs an outer join across Users, ZwiftPower, and Zwift Racing to show
    all riders from any source, not just those with user accounts.

    Returns:
        List of MembershipReviewRider objects sorted by display name.

    """
    from django.db.models import Max
    from django_countries import countries

    # Query each source independently
    users = User.objects.filter(zwid__isnull=False).values(
        "id", "zwid", "first_name", "last_name", "discord_id", "discord_nickname", "discord_username",
        "gender", "zwid_verified", "zwid_verification_method",
        # Member profile fields
        "birth_year", "city", "country", "timezone",
        # Equipment
        "trainer", "powermeter", "dual_recording", "heartrate_monitor", "has_jersey",
        # Emergency contact
        "emergency_contact_name", "emergency_contact_phone",
    )
    zp_riders = ZPTeamRiders.objects.all().values("zwid", "name", "div", "divw", "date_left")
    zr_riders = ZRRider.objects.all().values("zwid", "name", "date_left")

    # Build lookup dicts
    user_by_zwid: dict[int, dict] = {u["zwid"]: u for u in users}
    zp_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zp_riders}
    zr_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zr_riders}

    # Get result counts and last result date per rider
    result_stats = (
        ZPRiderResults.objects
        .values("zwid")
        .annotate(
            count=Count("id"),
            last_result=Max("event__event_date")
        )
    )
    results_by_zwid: dict[int, dict] = {
        r["zwid"]: {"count": r["count"], "last_result": r["last_result"]}
        for r in result_stats
    }

    # Collect ALL unique zwids (outer join)
    zwid_set: set[int] = set()
    zwid_set.update(user_by_zwid.keys())
    zwid_set.update(zp_by_zwid.keys())
    zwid_set.update(zr_by_zwid.keys())

    # Build MembershipReviewRider objects for each unique zwid
    riders: list[MembershipReviewRider] = []
    for zwid in zwid_set:
        rider = MembershipReviewRider(zwid=zwid, has_account=False)

        # Add user data if exists
        if zwid in user_by_zwid:
            u = user_by_zwid[zwid]
            rider.user_id = u["id"]
            rider.has_account = True
            rider.zwid_verified = verification_accepted(u)
            rider.discord_id = u["discord_id"] or ""
            rider.discord_nickname = u["discord_nickname"] or u["discord_username"] or ""

            # Build full name
            first = u["first_name"] or ""
            last = u["last_name"] or ""
            rider.full_name = f"{first} {last}".strip()

            # Normalize gender
            if u["gender"] == "male":
                rider.gender = "M"
            elif u["gender"] == "female":
                rider.gender = "F"

            # Member profile fields
            rider.birth_year = u["birth_year"]
            rider.city = u["city"] or ""
            rider.country = str(u["country"]) if u["country"] else ""
            rider.country_name = countries.name(u["country"]) if u["country"] else ""
            rider.timezone = u["timezone"] or ""

            # Equipment
            rider.trainer = u["trainer"] or ""
            rider.powermeter = u["powermeter"] or ""
            rider.dual_recording = u["dual_recording"]
            rider.heartrate_monitor = u["heartrate_monitor"] or ""
            rider.has_jersey = u["has_jersey"]

            # Emergency contact
            rider.emergency_contact_name = u["emergency_contact_name"] or ""
            rider.emergency_contact_phone = u["emergency_contact_phone"] or ""

        # Add ZP data
        if zwid in zp_by_zwid:
            zp = zp_by_zwid[zwid]
            rider.in_zwiftpower = True
            rider.zp_name = zp["name"]
            rider.zp_div = zp["div"]
            rider.zp_date_left = zp["date_left"]

            # Fall back to ZP gender if user gender not set
            if not rider.gender:
                rider.gender = "F" if zp["divw"] and zp["divw"] > 0 else "M"

        # Add ZR data
        if zwid in zr_by_zwid:
            zr = zr_by_zwid[zwid]
            rider.in_zwiftracing = True
            rider.zr_name = zr["name"]
            rider.zr_date_left = zr["date_left"]

        # Add results data
        if zwid in results_by_zwid:
            stats = results_by_zwid[zwid]
            rider.result_count = stats["count"]
            rider.last_result_date = stats["last_result"]

        riders.append(rider)

    # Enrich with guild member data and add guild-only members
    guild_members = GuildMember.objects.filter(
        date_left__isnull=True, is_bot=False,
    ).values("discord_id", "nickname", "username", "display_name", "user_id", "joined_at")
    guild_by_discord_id: dict[str, dict] = {gm["discord_id"]: gm for gm in guild_members}

    # Build set of discord_ids already represented
    seen_discord_ids: set[str] = set()
    for rider in riders:
        if rider.discord_id and rider.discord_id in guild_by_discord_id:
            gm = guild_by_discord_id[rider.discord_id]
            rider.guild_nickname = gm["nickname"] or ""
            rider.guild_joined_at = gm["joined_at"]
            rider.in_guild = True
            seen_discord_ids.add(rider.discord_id)

    # Also check users WITHOUT zwid that are linked to guild members
    users_no_zwid = {
        u["discord_id"]: u
        for u in User.objects.filter(
            zwid__isnull=True, discord_id__isnull=False,
        ).exclude(discord_id="").values(
            "id", "first_name", "last_name", "discord_id", "discord_nickname", "discord_username",
            "gender", "zwid_verified", "zwid_verification_method",
            "birth_year", "city", "country", "timezone",
            "trainer", "powermeter", "dual_recording", "heartrate_monitor", "has_jersey",
            "emergency_contact_name", "emergency_contact_phone",
        )
        if u["discord_id"]
    }

    # Add guild members not yet represented
    for discord_id, gm in guild_by_discord_id.items():
        if discord_id in seen_discord_ids:
            continue
        rider = MembershipReviewRider(zwid=0, has_account=False, in_guild=True)
        rider.discord_id = discord_id
        rider.guild_nickname = gm["nickname"] or ""
        rider.guild_joined_at = gm["joined_at"]
        rider.discord_nickname = gm["display_name"] or gm["username"] or ""

        # Check if there's a user without ZWID linked to this guild member
        if discord_id in users_no_zwid:
            u = users_no_zwid[discord_id]
            rider.user_id = u["id"]
            rider.has_account = True
            rider.discord_nickname = u["discord_nickname"] or u["discord_username"] or rider.discord_nickname
            first = u["first_name"] or ""
            last = u["last_name"] or ""
            rider.full_name = f"{first} {last}".strip()
            if u["gender"] == "male":
                rider.gender = "M"
            elif u["gender"] == "female":
                rider.gender = "F"
            rider.birth_year = u["birth_year"]
            rider.city = u["city"] or ""
            rider.country = str(u["country"]) if u["country"] else ""
            rider.country_name = countries.name(u["country"]) if u["country"] else ""
            rider.timezone = u["timezone"] or ""
            rider.trainer = u["trainer"] or ""
            rider.powermeter = u["powermeter"] or ""
            rider.dual_recording = u["dual_recording"]
            rider.heartrate_monitor = u["heartrate_monitor"] or ""
            rider.has_jersey = u["has_jersey"]
            rider.emergency_contact_name = u["emergency_contact_name"] or ""
            rider.emergency_contact_phone = u["emergency_contact_phone"] or ""
            rider.zwid_verified = verification_accepted(u)

        riders.append(rider)

    # Sort by best available name
    sorted_riders = sorted(
        riders,
        key=lambda r: (r.full_name or r.discord_nickname or r.guild_nickname or r.zp_name or r.zr_name or "").lower(),
    )

    logfire.debug(
        "Membership review data loaded",
        total_riders=len(sorted_riders),
        users_with_zwid=len(user_by_zwid),
        zp_riders=len(zp_by_zwid),
        zr_riders=len(zr_by_zwid),
        guild_members=len(guild_by_discord_id),
    )

    return sorted_riders
