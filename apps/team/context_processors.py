"""Context processors for team app."""

import logfire
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpRequest

from apps.team.models import RaceReadyRecord

PENDING_VERIFICATION_CACHE_PREFIX = "pending_verification_count:v1"
PENDING_VERIFICATION_CACHE_TIMEOUT = 60  # seconds


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
