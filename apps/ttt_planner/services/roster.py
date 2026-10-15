"""Look up a team rider's weight, height and power figures for a plan.

Everything comes from the rider profile cache (``apps.rider_data.RiderProfile``), which the
zauth service fills from Zwift, ZwiftPower and Zwift Racing, merged into one document per
rider. Nothing here calls out: it reads what ``sync_rider_profiles`` last stored.

Only the team's riders are offered (``rider_data.services.zwids_to_refresh``, the same set
the team roster lists). The cache keeps a rider who has left until it is purged, and can
hold riders from other teams, so "every row we hold" is not the team.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.rider_data.models import RiderProfile
from apps.rider_data.services import zwids_to_refresh
from apps.ttt_planner.services.compute import MIN_CREDIBLE_W_PRIME_J


@dataclass
class RiderData:
    """A team rider's figures, as a plan snapshots them."""

    zwid: int
    name: str
    weight_kg: float | None
    height_cm: int | None
    ftp_w: int | None
    category: str
    cp_w: int | None = None
    w_prime_j: int | None = None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _rider_data(profile: RiderProfile) -> RiderData:
    """Read a profile into a plan's figures.

    FTP is ``zftp``, as on the team roster: zauth resolves it from Zwift, ZwiftPower and
    Zwift Racing in turn, while ``ftp`` (the in-game FTP) is there only for riders who
    connected through zauth, so it stands in only when ``zftp`` is missing. CP and W′ come
    from Zwift Racing's power block; a W′ of 0 there means "unknown", as anything below
    :data:`MIN_CREDIBLE_W_PRIME_J` does.

    Args:
        profile: The rider's cached profile.

    Returns:
        The rider's :class:`RiderData`.

    """
    power = profile.power_extras or {}
    cp = _number(power.get("cp"))
    awc = _number(power.get("awc"))
    ftp = profile.zftp if profile.zftp is not None else profile.ftp
    return RiderData(
        zwid=profile.zwid,
        name=profile.name or str(profile.zwid),
        weight_kg=round(profile.weight_kg, 1) if profile.weight_kg is not None else None,
        height_cm=round(profile.height_cm) if profile.height_cm is not None else None,
        ftp_w=round(ftp) if ftp is not None else None,
        category=profile.category_open or profile.category_women,
        cp_w=round(cp) if cp is not None and cp > 0 else None,
        w_prime_j=round(awc) if awc is not None and awc >= MIN_CREDIBLE_W_PRIME_J else None,
    )


def get_rider_data(zwids: list[int]) -> dict[int, RiderData]:
    """Fetch the figures of the team riders among some zwids.

    Args:
        zwids: Zwift IDs to look up.

    Returns:
        Mapping of zwid -> RiderData for every one on the team with a cached profile.

    """
    team = set(zwids_to_refresh())
    wanted = [zwid for zwid in zwids if zwid in team]
    if not wanted:
        return {}
    return {profile.zwid: _rider_data(profile) for profile in RiderProfile.objects.filter(zwid__in=wanted)}


def search_riders(query: str, *, limit: int = 8, exclude_zwids: set[int] | None = None) -> list[RiderData]:
    """Search the team's riders by name, or by an exact zwid, for the add-rider autocomplete.

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

    matches = Q(name__icontains=query)
    if query.isdigit():
        matches |= Q(zwid=int(query))
    team = set(zwids_to_refresh()) - (exclude_zwids or set())
    found = RiderProfile.objects.filter(matches).order_by("name").values_list("zwid", flat=True)
    zwids = [zwid for zwid in found if zwid in team][:limit]

    profiles = RiderProfile.objects.in_bulk(zwids)
    return [_rider_data(profiles[zwid]) for zwid in zwids if zwid in profiles]
