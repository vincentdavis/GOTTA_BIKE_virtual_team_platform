"""Look up rider weight / FTP / height from existing team data.

Merges ZwiftPower (`ZPTeamRiders`) and Zwift Racing (`ZRRider`) records by zwid,
preferring whichever source has a value. No new external API calls -- this reads
the data the platform already syncs.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider


@dataclass
class RiderData:
    """Merged rider attributes from team data sources."""

    zwid: int
    name: str
    weight_kg: float | None
    height_cm: int | None
    ftp_w: int | None
    category: str


def _category(zp: ZPTeamRiders | None, zr: ZRRider | None) -> str:
    """Derive a display category from ZP division or ZR category.

    Args:
        zp: ZwiftPower record or None.
        zr: Zwift Racing record or None.

    Returns:
        A short category label (may be empty).

    """
    if zr and zr.zp_category:
        return zr.zp_category
    if zp and zp.div:
        # ZP div 5/10/20/30/40/50 -> A+/A/B/C/D/E
        return {5: "A+", 10: "A", 20: "B", 30: "C", 40: "D", 50: "E"}.get(zp.div, "")
    return ""


def get_rider_data(zwids: list[int]) -> dict[int, RiderData]:
    """Fetch merged rider data for a list of zwids.

    Args:
        zwids: Zwift IDs to look up.

    Returns:
        Mapping of zwid -> RiderData for every zwid found in either source.

    """
    if not zwids:
        return {}

    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)}

    result: dict[int, RiderData] = {}
    for zwid in set(zp_by_zwid) | set(zr_by_zwid):
        zp = zp_by_zwid.get(zwid)
        zr = zr_by_zwid.get(zwid)
        weight = (zp and zp.weight) or (zr and zr.weight)
        ftp = (zp and zp.ftp) or (zr and zr.zp_ftp)
        height = zr.height if zr else None
        name = (zp and zp.name) or (zr and zr.name) or str(zwid)
        result[zwid] = RiderData(
            zwid=zwid,
            name=name,
            weight_kg=float(weight) if weight is not None else None,
            height_cm=int(height) if height is not None else None,
            ftp_w=int(ftp) if ftp is not None else None,
            category=_category(zp, zr),
        )
    return result


def search_riders(query: str, *, limit: int = 8, exclude_zwids: set[int] | None = None) -> list[RiderData]:
    """Search team riders by name or zwid for the add-rider autocomplete.

    Args:
        query: Search string (name fragment or numeric zwid).
        limit: Maximum results to return.
        exclude_zwids: Zwids already on the plan, omitted from results.

    Returns:
        A list of RiderData ordered by name.

    """
    query = query.strip()
    if len(query) < 2:
        return []

    exclude_zwids = exclude_zwids or set()

    name_q = Q(name__icontains=query)
    if query.isdigit():
        name_q |= Q(zwid=int(query))

    zp_qs = ZPTeamRiders.objects.filter(name_q).exclude(zwid__in=exclude_zwids).order_by("name")[: limit * 2]
    zwids = [r.zwid for r in zp_qs][:limit]

    # Fall back to ZR for riders not on the ZP team table.
    if len(zwids) < limit:
        zr_qs = (
            ZRRider.objects
            .filter(name_q)
            .exclude(zwid__in=set(zwids) | exclude_zwids)
            .order_by("name")[: limit - len(zwids)]
        )
        zwids.extend(r.zwid for r in zr_qs)

    data = get_rider_data(zwids)
    return [data[z] for z in zwids if z in data]
