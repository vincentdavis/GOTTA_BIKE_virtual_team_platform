"""Context processors for events app."""

import logfire
from django.core.cache import cache
from django.http import HttpRequest

from apps.events.models import AvailabilityGrid, AvailabilityResponse, SquadMember

PENDING_AVAILABILITY_CACHE_PREFIX = "pending_availability_count:v1"
PENDING_AVAILABILITY_CACHE_TIMEOUT = 60  # seconds


def pending_availability_count(request: HttpRequest) -> dict[str, int]:
    """Expose the count of published availability grids the user has not responded to.

    Counts published AvailabilityGrid rows that belong to a squad the user is an
    active member of, excluding any grid where the user has already submitted an
    AvailabilityResponse. Used to drive a notification dot on the user-menu
    avatar and a count badge next to "My Events" in the dropdown.

    Returns 0 (and skips the database query) for anonymous users so the cost is
    zero on logged-out pageviews. For authenticated users with no squad
    memberships, returns 0 after a single cheap query.

    Per-user cache with a short TTL — base.html renders on every page, so we
    collapse repeated calls into one set of queries per user per minute.

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with ``pending_availability_count`` (int).

    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"pending_availability_count": 0}

    cache_key = f"{PENDING_AVAILABILITY_CACHE_PREFIX}:{user.pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"pending_availability_count": cached}

    with logfire.span("pending_availability_count", user_id=user.pk):
        squad_ids = list(
            SquadMember.objects
            .filter(user=user, status=SquadMember.Status.MEMBER)
            .values_list("squad_id", flat=True)
        )
        if not squad_ids:
            count = 0
        else:
            responded_grid_ids = list(
                AvailabilityResponse.objects
                .filter(user=user)
                .values_list("grid_id", flat=True)
            )
            count = (
                AvailabilityGrid.objects
                .filter(status=AvailabilityGrid.Status.PUBLISHED, squad_id__in=squad_ids)
                .exclude(pk__in=responded_grid_ids)
                .count()
            )

    cache.set(cache_key, count, PENDING_AVAILABILITY_CACHE_TIMEOUT)
    logfire.debug("pending_availability_count computed", user_id=user.pk, count=count)
    return {"pending_availability_count": count}
