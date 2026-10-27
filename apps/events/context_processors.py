"""Context processors for events app."""

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import logfire
from django.core.cache import cache
from django.utils import timezone

from apps.events import series
from apps.events.models import AvailabilityGrid, AvailabilityResponse, SheetStage, SquadMember

if TYPE_CHECKING:
    from django.http import HttpRequest

PENDING_AVAILABILITY_CACHE_PREFIX = "pending_availability_count:v3"
PENDING_AVAILABILITY_CACHE_TIMEOUT = 60  # seconds


def pending_availability_count(request: HttpRequest) -> dict[str, int]:
    """Expose the count of published availability grids the user has not responded to.

    Counts published AvailabilityGrid rows that belong to a squad the user is an
    active member of, excluding any grid where the user has already submitted an
    AvailabilityResponse and any grid whose availability window has already ended
    (``end_date`` before the user's local today) — mirroring the My Events page,
    where ended forms are hidden by default. Used to drive a notification dot on
    the user-menu avatar and a count badge next to "My Events" in the dropdown.

    A race series sheet counts while any stage still to start has no answer for its
    current time -- unanswered, or answered before the captain moved it -- so a response
    alone does not clear it.

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
            user_tz = getattr(user, "timezone", "") or ""
            now = timezone.now()
            try:
                today_local = now.astimezone(ZoneInfo(user_tz)).date() if user_tz else now.date()
            except Exception:
                today_local = now.date()
            responded_grid_ids = set(
                AvailabilityResponse.objects
                .filter(user=user)
                .values_list("grid_id", flat=True)
            )
            open_grids = list(
                AvailabilityGrid.objects
                .filter(
                    status=AvailabilityGrid.Status.PUBLISHED,
                    squad_id__in=squad_ids,
                    end_date__gte=today_local,
                )
                .values_list("pk", "kind")
            )
            series_ids = [pk for pk, kind in open_grids if kind == AvailabilityGrid.Kind.SERIES]
            count = sum(
                1 for pk, kind in open_grids if kind != AvailabilityGrid.Kind.SERIES and pk not in responded_grid_ids
            )
            if series_ids:
                answers = dict(
                    AvailabilityResponse.objects
                    .filter(user=user, grid_id__in=series_ids)
                    .values_list("grid_id", "stage_answers")
                )
                stages: dict = {pk: [] for pk in series_ids}
                for sheet_stage in SheetStage.objects.filter(grid_id__in=series_ids):
                    stages[sheet_stage.grid_id].append(sheet_stage)
                count += sum(
                    1 for pk in series_ids if series.needs_answer(stages[pk], answers.get(pk), after=now)
                )

    cache.set(cache_key, count, PENDING_AVAILABILITY_CACHE_TIMEOUT)
    logfire.debug("pending_availability_count computed", user_id=user.pk, count=count)
    return {"pending_availability_count": count}
