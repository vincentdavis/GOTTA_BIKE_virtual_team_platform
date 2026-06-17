"""Background tasks for the ladder planner cache.

``warm_club`` fetches a whole club roster into the cache so opponents from that
club become searchable without further live calls. ``refresh_cached_clubs``
keeps in-use clubs current on a schedule. Both respect ZR's ~1-request/60s limit
by re-enqueuing (warm_club) or spacing calls (refresh_cached_clubs).
"""

from __future__ import annotations

from datetime import timedelta

import logfire
from django.db.models import Q
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.ladder_planner.models import CachedClub, CachedRider, LadderRider, Side
from apps.ladder_planner.services import cache, normalize
from apps.zwiftracing.zr_client import get_club

# A full club page from ZR caps at 1000 riders; >= this means paginate.
_PAGE_FULL = 999
# Spacing after a successful paginated page (ZR limit is ~1 req / 60s).
_PAGE_DELAY_S = 630
# A cached club is refreshed at most this often.
_CLUB_STALE_DAYS = 7
# Only clubs used by a matchup edited within this window are kept warm.
_ACTIVE_MATCHUP_DAYS = 90


@task
def warm_club(club_id: int, from_id: int | None = None, _accumulated: int = 0) -> dict:
    """Fetch a club's roster into the cache, paginating and respecting rate limits.

    Args:
        club_id: Club ID to fetch.
        from_id: Pagination cursor (last rider id of the previous page).
        _accumulated: Riders cached so far across prior pages (internal).

    Returns:
        A status dict describing the outcome.

    """
    with logfire.span("ladder warm_club", club_id=club_id, from_id=from_id):
        status_code, data = get_club(club_id, from_id)

        if status_code == 429:
            retry_after = int(data.get("retryAfter", 600)) if isinstance(data, dict) else 600
            run_at = timezone.now() + timedelta(seconds=retry_after)
            logfire.warning("warm_club rate limited", club_id=club_id, retry_after=retry_after)
            warm_club.using(run_after=run_at).enqueue(club_id, from_id, _accumulated)
            return {"status": "rate_limited", "club_id": club_id, "retry_after": retry_after}

        riders = (data or {}).get("riders", []) if isinstance(data, dict) else []
        club_name = (data or {}).get("name", "") if isinstance(data, dict) else ""

        datas = [normalize.from_api(r) for r in riders if isinstance(r, dict) and r.get("riderId")]
        written = cache.upsert_riders(datas, source=CachedRider.Source.CLUB)
        total = _accumulated + written

        # Paginate if the page came back full.
        if len(riders) >= _PAGE_FULL and riders[-1].get("riderId"):
            run_at = timezone.now() + timedelta(seconds=_PAGE_DELAY_S)
            warm_club.using(run_after=run_at).enqueue(club_id, riders[-1]["riderId"], total)
            logfire.info("warm_club paginating", club_id=club_id, page_riders=len(riders), total=total)
            return {"status": "paginating", "club_id": club_id, "cached": total}

        cache.mark_club_refreshed(club_id, name=club_name, rider_count=total)
        logfire.info("warm_club complete", club_id=club_id, cached=total)
        return {"status": "complete", "club_id": club_id, "cached": total}


def _active_club_ids(now) -> set[int]:
    """Return club ids referenced by opponents in recently-edited matchups.

    Args:
        now: Current time.

    Returns:
        Set of club ids worth keeping warm.

    """
    cutoff = now - timedelta(days=_ACTIVE_MATCHUP_DAYS)
    club_ids: set[int] = set()
    riders = LadderRider.objects.filter(side=Side.OPPONENT, matchup__updated_at__gte=cutoff).only("zr_data")
    for rider in riders.iterator():
        club_id = (rider.zr_data or {}).get("club_id")
        if club_id:
            club_ids.add(club_id)
    return club_ids


@task
def refresh_cached_clubs() -> dict:
    """Enqueue a background refresh for each due, in-use cached club.

    A club is refreshed if it is referenced by a matchup edited in the last
    ``_ACTIVE_MATCHUP_DAYS`` days, has ``auto_refresh`` on, and hasn't been
    refreshed within ``_CLUB_STALE_DAYS`` days. Each due club is handed to
    ``warm_club``, whose own 429 backoff serialises the calls within ZR's rate
    limit — so this task never blocks a worker.

    Returns:
        A status dict with the number of clubs enqueued.

    """
    with logfire.span("ladder refresh_cached_clubs"):
        now = timezone.now()
        active = _active_club_ids(now)
        if not active:
            return {"status": "complete", "enqueued": 0}

        stale_cutoff = now - timedelta(days=_CLUB_STALE_DAYS)
        due = CachedClub.objects.filter(club_id__in=active, auto_refresh=True).filter(
            Q(last_refreshed_at__isnull=True) | Q(last_refreshed_at__lt=stale_cutoff)
        )
        club_ids = list(due.values_list("club_id", flat=True))
        for club_id in club_ids:
            warm_club.enqueue(club_id)

        logfire.info("refresh_cached_clubs enqueued", count=len(club_ids), active=len(active))
        return {"status": "complete", "enqueued": len(club_ids)}
