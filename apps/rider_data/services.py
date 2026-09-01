"""Turning a zauth profile document into a stored row, and deciding whose to fetch."""

from __future__ import annotations

from datetime import datetime

import logfire
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.accounts.models import GuildMember, User
from apps.rider_data.models import RiderProfile


def _as_datetime(value: object) -> datetime | None:
    """Parse an ISO date or datetime from the service into an aware datetime.

    The clubs block carries dates (``event_date``) while the sources block carries
    datetimes, so both shapes arrive and a date must not be dropped for lacking a time.

    Args:
        value: An ISO string, or anything else.

    Returns:
        An aware datetime, or None if unparseable.

    """
    if not isinstance(value, str) or not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        as_date = parse_date(value)
        if as_date is None:
            return None
        parsed = datetime.combine(as_date, datetime.min.time())
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def last_race_from(profile: dict) -> datetime | None:
    """Derive the rider's most recent known race from the clubs block.

    ``clubs.known[].last_seen`` is ``Max(event_date)`` over that rider's ZwiftPower results
    for one club, so the maximum across their clubs is the last time we saw them race at all.
    This is the retention anchor, which is why it is computed here rather than left to a
    caller: nothing else in the document dates the rider's activity.

    Args:
        profile: A ProfileFull document.

    Returns:
        The latest known race datetime, or None when we have no race history for them.

    """
    known = ((profile.get("clubs") or {}).get("known")) or []
    seen = [dt for dt in (_as_datetime(club.get("last_seen")) for club in known) if dt is not None]
    return max(seen) if seen else None


def _num(value: object) -> float | None:
    """Coerce a numeric field, treating anything non-numeric as absent.

    Args:
        value: The raw value.

    Returns:
        A float, or None.

    """
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def to_row(profile: dict) -> dict | None:
    """Map a ProfileFull document onto RiderProfile field values.

    Only the promoted columns are pulled out; everything else is kept whole in ``payload``.
    That split is the app's central rule, so the mapping deliberately does not "helpfully"
    flatten anything extra -- a value that gains a column should do so by a decision, not by
    appearing here.

    Args:
        profile: A ProfileFull document.

    Returns:
        Field values keyed for ``update_or_create``, or None if the document has no zwid.

    """
    zwid = profile.get("zwid")
    if not isinstance(zwid, int):
        return None

    identity = profile.get("identity") or {}
    physical = profile.get("physical") or {}
    power = profile.get("power") or {}
    category = profile.get("category") or {}
    ratings = profile.get("ratings") or {}
    phenotype = profile.get("phenotype") or {}
    current_club = (profile.get("clubs") or {}).get("current") or {}

    return {
        "zwid": zwid,
        "zwift_user_id": profile.get("zwift_user_id") or "",
        "name": identity.get("name") or "",
        "gender": identity.get("gender") or "",
        "country": identity.get("country") or "",
        "age": identity.get("age") or "",
        "weight_kg": _num(physical.get("weight_kg")),
        "height_cm": _num(physical.get("height_cm")),
        "ftp": _num(power.get("ftp")),
        "zftp": _num(power.get("zftp")),
        "category_open": category.get("open") or "",
        "category_women": category.get("women") or "",
        "category_racing": category.get("racing") or "",
        "velo": _num(ratings.get("velo")),
        "zwift_racing_score": _num(ratings.get("zwift_racing_score")),
        "zp_skill": _num(ratings.get("zp_skill")),
        "compound_score": _num(ratings.get("compound_score")),
        "phenotype_value": (phenotype.get("value") if isinstance(phenotype, dict) else "") or "",
        "club_id": current_club.get("id"),
        "club_name": current_club.get("name") or "",
        "payload": profile,
        "sources": profile.get("sources") or {},
        "has_account": profile.get("has_account") or {},
        "last_race_at": last_race_from(profile),
        "fetched_at": timezone.now(),
    }


def store_profiles(profiles: list[dict]) -> dict[str, int]:
    """Upsert fetched profiles into the cache.

    Args:
        profiles: ProfileFull documents from the service.

    Returns:
        Counts of rows ``created``, ``updated`` and documents ``skipped``.

    """
    created = updated = skipped = 0
    for profile in profiles:
        row = to_row(profile)
        if row is None:
            skipped += 1
            continue
        zwid = row.pop("zwid")
        _, was_created = RiderProfile.objects.update_or_create(zwid=zwid, defaults=row)
        created += was_created
        updated += not was_created

    logfire.info("Stored rider profiles", created=created, updated=updated, skipped=skipped)
    return {"created": created, "updated": updated, "skipped": skipped}


def zwids_to_refresh() -> list[int]:
    """Return the riders whose profiles we want kept current.

    Everyone registered here who has a Zwift id. That is wider than the zauth-connected set
    on purpose: a member who never linked Zwift still races, still appears on the roster, and
    still needs a profile. It is narrower than "every rider the service knows about", which
    is the point -- the cache should not accumulate people we have no reason to hold.

    The connected set is fetched separately by ``connected_app`` and does not need listing
    here, since the service resolves it.

    Returns:
        Distinct zwids, sorted.

    """
    return sorted(User.objects.filter(zwid__isnull=False).values_list("zwid", flat=True).distinct())


def protected_zwids() -> set[int]:
    """Return the riders who must never be evicted, whatever their race activity.

    Current team members, resolved through Discord guild membership -- the signal the team
    actually operates on, and the one whose ``date_left`` is genuinely written. Evicting a
    member's profile would only cause the next sync to re-create it, so this prevents churn
    as much as data loss.

    Returns:
        The zwids of riders with an open guild membership.

    """
    zwids = (
        GuildMember.objects.filter(date_left__isnull=True, user__isnull=False, user__zwid__isnull=False)
        .values_list("user__zwid", flat=True)
        .distinct()
    )
    return set(zwids)
