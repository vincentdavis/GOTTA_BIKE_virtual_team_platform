"""Shared cache of opponent (non-team) Zwift Racing data.

DB-only persistence layer for ``CachedRider`` / ``CachedClub`` — no ZR API calls
live here (those stay in ``roster``/``tasks`` and write through these helpers).
The opponent search reads this cache so it never hits the rate-limited API; a
cache miss is what triggers a live fetch.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.ladder_planner.models import CachedClub, CachedRider


def get_snapshot(zwid: int) -> dict[str, Any] | None:
    """Return the cached normalized snapshot for a zwid, if present.

    Args:
        zwid: Zwift ID.

    Returns:
        The normalized rider dict, or None on a cache miss.

    """
    rider = CachedRider.objects.filter(zwid=zwid).first()
    return rider.zr_data if rider else None


def upsert_riders(datas: list[dict[str, Any]], *, source: str, now=None) -> int:
    """Write normalized rider dicts into the cache, tracking their clubs.

    Args:
        datas: Normalized rider dicts (from ``services.normalize``).
        source: Provenance label (``CachedRider.Source`` value).
        now: Timestamp to stamp (defaults to ``timezone.now()``).

    Returns:
        The number of riders written.

    """
    now = now or timezone.now()
    club_names: dict[int, str] = {}
    written = 0
    for data in datas:
        zwid = data.get("zwid")
        if not zwid:
            continue
        club_id = data.get("club_id")
        club_name = data.get("club_name") or ""
        CachedRider.objects.update_or_create(
            zwid=zwid,
            defaults={
                "name": data.get("name") or str(zwid),
                "club_id": club_id,
                "club_name": club_name,
                "zr_data": data,
                "source": source,
                "fetched_at": now,
            },
        )
        if club_id:
            club_names.setdefault(club_id, club_name)
        written += 1

    # Ensure each referenced club is tracked for the background refresh.
    for club_id, club_name in club_names.items():
        track_club(club_id, name=club_name)
    return written


def track_club(club_id: int, *, name: str = "") -> CachedClub:
    """Ensure a club is tracked for background refresh, updating its name.

    Args:
        club_id: Club ID.
        name: Club name (updated if non-empty and changed).

    Returns:
        The tracked CachedClub.

    """
    club, created = CachedClub.objects.get_or_create(club_id=club_id, defaults={"name": name})
    if not created and name and club.name != name:
        club.name = name
        club.save(update_fields=["name"])
    return club


def mark_club_refreshed(club_id: int, *, name: str, rider_count: int, now=None, error: str = "") -> None:
    """Stamp a club after a full refresh.

    Args:
        club_id: Club ID.
        name: Club name from the refresh payload.
        rider_count: Riders cached in this refresh.
        now: Timestamp (defaults to ``timezone.now()``).
        error: Error message to record (empty clears it).

    """
    now = now or timezone.now()
    CachedClub.objects.update_or_create(
        club_id=club_id,
        defaults={"name": name, "rider_count": rider_count, "last_refreshed_at": now, "last_error": error},
    )


def search(query: str, *, limit: int = 10, exclude_zwids: set[int] | None = None) -> list[dict[str, Any]]:
    """Search cached opponent riders by name or zwid (no ZR call).

    Args:
        query: Search string (name fragment or numeric zwid).
        limit: Maximum results.
        exclude_zwids: Zwids already on the matchup, omitted.

    Returns:
        A list of ``{"zwid", "name", "club_name", "category", "rating", "age_days"}``
        dicts ordered by name.

    """
    query = query.strip()
    if len(query) < 2:
        return []

    exclude_zwids = exclude_zwids or set()
    name_q = Q(name__icontains=query)
    if query.isdigit():
        name_q |= Q(zwid=int(query))

    now = timezone.now()
    riders = CachedRider.objects.filter(name_q).exclude(zwid__in=exclude_zwids).order_by("name")[:limit]
    return [
        {
            "zwid": r.zwid,
            "name": r.name,
            "club_name": r.club_name,
            "category": (r.zr_data or {}).get("zp_category") or "",
            "rating": (r.zr_data or {}).get("rating_current"),
            "age_days": (now - r.fetched_at).days,
        }
        for r in riders
    ]
