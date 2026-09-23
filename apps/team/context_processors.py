"""Context processors for team app."""

from typing import TYPE_CHECKING

import logfire
from django.core.cache import cache

from apps.team.models import RaceReadyRecord
from apps.team.services import records_decidable_by

if TYPE_CHECKING:
    from django.http import HttpRequest

# v2: counts only records the reviewer may DECIDE (Other needs an admin, Power may need the team),
# not every record they may open. A new meaning, so a new key.
PENDING_VERIFICATION_CACHE_PREFIX = "pending_verification_count:v2"
PENDING_VERIFICATION_CACHE_TIMEOUT = 60  # seconds

# v2: a weight record the rider's OTHER weight record supersedes no longer warns, where the
# category accepts either one. A different set of warnings, so a different key.
EXPIRING_VERIFICATION_CACHE_PREFIX = "expiring_verifications:v2"
EXPIRING_VERIFICATION_CACHE_TIMEOUT = 360  # seconds

# v2: the count now follows the rider's REQUIRED verifications (services.verification_coverage_bulk)
# instead of every type they hold, so the same squad yields a different number. A new meaning,
# so a new key -- otherwise captains read the old, wrong count for the whole cache window.
SQUAD_EXPIRING_CACHE_PREFIX = "squad_expiring_verifications:v2"
SQUAD_EXPIRING_CACHE_TIMEOUT = 360  # seconds


def pending_verification_count(request: HttpRequest) -> dict[str, int]:
    """Expose the count of pending verification records the user can decide.

    A badge is a request to act, so it counts only what this reviewer can clear: the rule is
    ``services.records_decidable_by`` -- same-gender, Other (admins) and Power (the team, when
    ``POWER_REQUIRES_PER_VER`` is on). Counting records they may open but not decide would
    hand reviewers a number they can never bring down. Superusers see every pending record.

    Returns 0 (and skips the database query) for anonymous users and users
    without ``approve_verification`` permission, so the cost is zero on the
    vast majority of pageviews.

    Per-user cache with a short TTL — the sidebar renders on every authenticated
    page, so we collapse repeated calls into one COUNT per user per minute.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with ``pending_verification_count`` (int).

    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.can_approve_verification:
        return {"pending_verification_count": 0}

    cache_key = f"{PENDING_VERIFICATION_CACHE_PREFIX}:{user.pk}:{user.gender or '_'}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"pending_verification_count": cached}

    with logfire.span("pending_verification_count", user_id=user.pk):
        qs = RaceReadyRecord.objects.filter(status=RaceReadyRecord.Status.PENDING)
        count = records_decidable_by(user, qs).count()

    cache.set(cache_key, count, PENDING_VERIFICATION_CACHE_TIMEOUT)
    logfire.debug("pending_verification_count computed", user_id=user.pk, count=count)
    return {"pending_verification_count": count}


def expiring_verifications(request: HttpRequest) -> dict:
    """Expose the current user's soon-to-expire Race Verified records.

    Drives the warning banner in ``base.html``. Records are first reconciled per
    ``verify_type`` (``covering_records_by_type``) so a type the rider has already
    renewed — a newer same-type record with more days left — never raises a warning
    for the old expiring record it replaced. A *type* counts as "expiring soon" when
    its longest-lived verified record has a finite expiry whose ``days_remaining``
    is inside ``services.is_expiring_soon`` — the single definition the
    ``warn_expiring_verifications`` DM task also uses, so the two cannot disagree
    about the boundary. That window is ``0..max(EXPIRE_WARNING_DAYS)``: a record
    expiring TODAY still counts, because the rider can still act on it. Records
    that have already lapsed are excluded — that is a "lost race ready" state
    needing different wording, not an "expiring" warning.

    Reconciliation then happens a second time ACROSS types, through
    ``services.superseded_weight_types``: in a category that accepts either weight
    (40/50) the two weight records are ONE requirement, so the one with less time
    left is superseded by the other exactly as an older same-type record is. Without
    it a rider with a valid ``weight_light`` was told to renew their lapsing
    ``weight_full`` to keep a status neither was about to cost them.

    Note this banner still warns about a type the rider's category does NOT require
    — a ``power`` record, or a ``weight_full`` held by a rider with no ZwiftPower
    category. It is evidence they chose to hold, it feeds Extra Verified, and losing
    the reminder for it would be a policy change rather than a fix. The captain list
    does drop those, because "who on my squad cannot race" is a different question.

    Note the banner is CONTINUOUS while the DM is discrete: the banner shows on
    every day inside the window, the task sends at most one DM per configured
    threshold. Same window, different cadence — deliberately, since nobody wants
    fifteen DMs.

    Returns an empty payload (and skips the database query) for anonymous users,
    so the cost is zero on anonymous pageviews. Per-user cache with a short TTL
    because the banner renders on every authenticated page.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with ``expiring_verifications`` — either ``None`` or a dict
        with ``count``, ``soonest_type`` (display label) and ``soonest_days``.

    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"expiring_verifications": None}

    cache_key = f"{EXPIRING_VERIFICATION_CACHE_PREFIX}:{user.pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        # ``False`` is the sentinel for "computed, nothing expiring".
        return {"expiring_verifications": cached or None}

    with logfire.span("expiring_verifications", user_id=user.pk):
        from django.utils import timezone

        from apps.team.services import (
            covering_records_by_type,
            get_user_required_verification_types,
            is_expiring_soon,
            record_days_left,
            superseded_weight_types,
            verify_type_validity_days,
        )

        records = user.race_ready_records.filter(status=RaceReadyRecord.Status.VERIFIED)
        # Reconcile per verify_type: a type is only "expiring" when its longest-lived
        # record is inside the window. This keeps a record the rider has already renewed
        # (a newer same-type record with more days left) from raising a false warning.
        covering = covering_records_by_type(records)
        # days_remaining reads all four Constance windows on EVERY access and Constance has no
        # cache backend here, so the four are read once for the whole banner instead of four
        # SELECTs per type the rider holds.
        validity_by_type = verify_type_validity_days()
        today = timezone.localdate()
        days_by_type = {vtype: record_days_left(record, validity_by_type, today) for vtype, record in covering.items()}
        # Then the same reconciliation ACROSS types, where a category accepts either weight:
        # a weight_full expiring in 5 days is not the rider's problem while a weight_light
        # covers that one requirement for 30 more. Without this the rider was told to renew a
        # record their better one had already superseded -- the false alarm their captain saw.
        superseded = superseded_weight_types(get_user_required_verification_types(user), days_by_type)
        # One shared definition with the DM task -- see services.is_expiring_soon. Both used
        # to parse EXPIRE_WARNING_DAYS separately while claiming to be in lockstep, and they
        # disagreed about the last day: the banner vanished when a record expired today, and
        # the rider lost the warning on the one day it was most urgent.
        expiring = [
            covering[vtype]
            for vtype, days in days_by_type.items()
            if vtype not in superseded and is_expiring_soon(days)
        ]
        payload: dict | bool = False
        if expiring:
            soonest = min(expiring, key=lambda r: days_by_type[r.verify_type])
            payload = {
                "count": len(expiring),
                "soonest_type": soonest.get_verify_type_display(),
                "soonest_days": days_by_type[soonest.verify_type],
            }

    cache.set(cache_key, payload, EXPIRING_VERIFICATION_CACHE_TIMEOUT)
    logfire.debug("expiring_verifications computed", user_id=user.pk, payload=payload)
    return {"expiring_verifications": payload or None}


def squad_expiring_verifications(request: HttpRequest) -> dict:
    """Expose how many of a captain's squad-mates cannot race, or soon will not be able to.

    The count follows the rider's REQUIRED verifications
    (``services.verification_coverage_bulk``, the race-ready rule itself), so it agrees with
    ``User.calculate_race_ready()``: a lapsed record of a type the rider's category does not
    need is not counted, a lapsed weight is not counted while the other weight type still
    satisfies an either-weight category, and a rider missing a required type IS counted even
    when they hold something else. The Race Verified BADGE is the cached
    ``User.is_race_ready``, so this can still name a rider the badge has not caught up with,
    for up to ``SCHEDULER_REFRESH_ALL_RACE_READY_HOURS`` after a record lapses.

    Drives the captain banner in ``base.html``. Only the count is cached and rendered; the
    names, links and days are fetched on click by ``squad_expiring_modal_view``. Note the
    summary itself IS computed here to get that count -- the row-building costs no extra
    queries, but this is why ``squad_expiring_summary`` hoists its Constance reads instead of
    leaving them to ``days_remaining``.

    Distinct from the rider's own ``expiring_verifications`` banner in both audience and
    tone: that one is "renew yours", this one is "go and nudge these people". They can both
    be showing at once, which is why they are separate banners rather than one merged count.

    Gated on ``team_member`` as well as authentication, deliberately matching
    ``squad_expiring_modal_view``: a banner the modal behind it would refuse leaves the dialog
    on its spinner forever, which is worse than no banner. Both run no query for users who
    fail the gate. Past that, the first query filters on an opt-in that is off by default, so
    for almost every team member this is one cheap miss per cache window.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with ``squad_expiring_verifications`` -- ``None``, or a dict with
        ``count`` (distinct riders to remind).

    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_team_member:
        return {"squad_expiring_verifications": None}

    cache_key = f"{SQUAD_EXPIRING_CACHE_PREFIX}:{user.pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        # ``False`` is the sentinel for "computed, nothing to remind about".
        return {"squad_expiring_verifications": cached or None}

    with logfire.span("squad_expiring_verifications", user_id=user.pk):
        from apps.team.services import squad_expiring_summary

        count = squad_expiring_summary(user)["rider_count"]
        payload: dict | bool = {"count": count} if count else False

    cache.set(cache_key, payload, SQUAD_EXPIRING_CACHE_TIMEOUT)
    logfire.debug("squad_expiring_verifications computed", user_id=user.pk, count=count)
    return {"squad_expiring_verifications": payload or None}
