"""Source riders for a matchup.

Our side comes from locally-synced ``ZRRider`` rows; opponents are fetched live
from the Zwift Racing API by zwid. Both are flattened via ``normalize`` so the
matchup stores one consistent snapshot shape.
"""

from __future__ import annotations

from typing import Any

import httpx
import logfire
from django.db.models import Q

from apps.ladder_planner.models import CachedRider
from apps.ladder_planner.services import cache, normalize
from apps.zwiftracing import zr_client
from apps.zwiftracing.models import ZRRider


def search_our_riders(query: str, *, limit: int = 10, exclude_zwids: set[int] | None = None) -> list[dict[str, Any]]:
    """Search our synced ZR riders by name or zwid for the add-rider autocomplete.

    Args:
        query: Search string (name fragment or numeric zwid).
        limit: Maximum results to return.
        exclude_zwids: Zwids already on the matchup, omitted from results.

    Returns:
        A list of ``{"zwid", "name", "category", "rating"}`` dicts ordered by name.

    """
    query = query.strip()
    if len(query) < 2:
        return []

    exclude_zwids = exclude_zwids or set()
    name_q = Q(name__icontains=query)
    if query.isdigit():
        name_q |= Q(zwid=int(query))

    riders = ZRRider.objects.filter(name_q).exclude(zwid__in=exclude_zwids).order_by("name")[:limit]
    return [
        {
            "zwid": r.zwid,
            "name": r.name,
            "category": r.zp_category or "",
            "rating": float(r.race_current_rating) if r.race_current_rating is not None else None,
        }
        for r in riders
    ]


def get_our_rider(zwid: int) -> dict[str, Any] | None:
    """Build the normalized snapshot for one of our riders.

    Args:
        zwid: Zwift ID of the rider in ``ZRRider``.

    Returns:
        The unified rider dict, or None if the rider is not synced.

    """
    rider = ZRRider.objects.filter(zwid=zwid).first()
    return normalize.from_zrrider(rider) if rider else None


def fetch_opponents(zwids: list[int]) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch opponent riders live from the Zwift Racing API and normalize them.

    Args:
        zwids: Zwift IDs to fetch (deduplicated by the caller as needed).

    Returns:
        Tuple of (normalized rider dicts, error message or None). On a rate
        limit the error names the retry-after seconds; the rider list is empty.

    """
    zwids = [z for z in dict.fromkeys(zwids) if z]
    if not zwids:
        return [], None

    try:
        status_code, payload = zr_client.get_riders(zwids)
    except httpx.HTTPStatusError as exc:
        logfire.error("ZR opponent fetch failed", error=str(exc), zwid_count=len(zwids))
        return [], f"Zwift Racing API error ({exc.response.status_code})."
    except httpx.HTTPError as exc:
        logfire.error("ZR opponent fetch failed", error=str(exc), zwid_count=len(zwids))
        return [], "Could not reach the Zwift Racing API. Try again."

    if status_code == 429:
        retry_after = payload.get("retryAfter", "a few") if isinstance(payload, dict) else "a few"
        logfire.warning("ZR opponent fetch rate limited", retry_after=retry_after)
        return [], f"Zwift Racing API rate limited. Try again in {retry_after} seconds."

    if not isinstance(payload, list):
        logfire.error("ZR opponent fetch unexpected payload", payload_type=type(payload).__name__)
        return [], "Unexpected response from the Zwift Racing API."

    riders = [normalize.from_api(item) for item in payload if isinstance(item, dict) and item.get("riderId")]
    cache.upsert_riders(riders, source=CachedRider.Source.RIDER)
    logfire.info("ZR opponents fetched", requested=len(zwids), returned=len(riders))
    return riders, None
