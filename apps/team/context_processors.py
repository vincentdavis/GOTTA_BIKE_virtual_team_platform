"""Context processors for team app."""

import logfire
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpRequest

from apps.team.models import RaceReadyRecord

PENDING_VERIFICATION_CACHE_PREFIX = "pending_verification_count:v1"
PENDING_VERIFICATION_CACHE_TIMEOUT = 60  # seconds

EXPIRING_VERIFICATION_CACHE_PREFIX = "expiring_verifications:v1"
EXPIRING_VERIFICATION_CACHE_TIMEOUT = 360  # seconds


def pending_verification_count(request: HttpRequest) -> dict[str, int]:
    """Expose the count of pending verification records the user can review.

    Mirrors the same-gender gate enforced by ``verification_records_view``: a
    record flagged ``same_gender=True`` is only counted for reviewers whose
    gender matches the record owner. Superusers see every pending record.

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
        if not user.is_superuser:
            qs = qs.filter(Q(same_gender=False) | Q(same_gender=True, user__gender=user.gender))
        count = qs.count()

    cache.set(cache_key, count, PENDING_VERIFICATION_CACHE_TIMEOUT)
    logfire.debug("pending_verification_count computed", user_id=user.pk, count=count)
    return {"pending_verification_count": count}


def expiring_verifications(request: HttpRequest) -> dict:
    """Expose the current user's soon-to-expire Race Verified records.

    Drives the warning banner in ``base.html``. A record counts as "expiring
    soon" when it is verified, has a finite expiry, and its ``days_remaining``
    falls in ``1..threshold`` where ``threshold`` is the largest value in the
    ``EXPIRE_WARNING_DAYS`` Constance list (the same window the
    ``warn_expiring_verifications`` DM task uses, so the web banner and the DMs
    stay in lockstep). Already-expired records (``days_remaining <= 0``) are
    excluded — those are a "lost race ready" state, not an "expiring" warning.

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

    import json

    from constance import config

    # EXPIRE_WARNING_DAYS is stored as a JSON string (e.g. "[15, 7, 3, 1]"); the
    # banner uses the same window as the warn_expiring_verifications DM task, so
    # we parse it the same way and take the largest value as the warning horizon.
    try:
        parsed = json.loads(config.EXPIRE_WARNING_DAYS)
    except (json.JSONDecodeError, TypeError) as e:
        logfire.error("Failed to parse EXPIRE_WARNING_DAYS config", error=str(e))
        parsed = [15]
    warning_days = [int(d) for d in parsed if isinstance(d, int) or str(d).strip().lstrip("-").isdigit()]
    threshold = max(warning_days) if warning_days else 15

    with logfire.span("expiring_verifications", user_id=user.pk):
        records = user.race_ready_records.filter(status=RaceReadyRecord.Status.VERIFIED)
        expiring = [
            r for r in records if (days := r.days_remaining) is not None and 0 < days <= threshold
        ]
        payload: dict | bool = False
        if expiring:
            soonest = min(expiring, key=lambda r: r.days_remaining)
            payload = {
                "count": len(expiring),
                "soonest_type": soonest.get_verify_type_display(),
                "soonest_days": soonest.days_remaining,
            }

    cache.set(cache_key, payload, EXPIRING_VERIFICATION_CACHE_TIMEOUT)
    logfire.debug("expiring_verifications computed", user_id=user.pk, payload=payload)
    return {"expiring_verifications": payload or None}
