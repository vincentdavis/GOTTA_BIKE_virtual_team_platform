"""Turning a zauth profile document into a stored row, and deciding whose to fetch."""

from __future__ import annotations

from datetime import datetime, timedelta

import logfire
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.accounts.models import GuildMember, User
from apps.rider_data.models import RiderProfile

# How long a ZwiftRacing club row may go untouched before we stop treating the rider as in the
# club. The club sync rewrites every rider it lists on each run, so an untouched row means the
# club stopped listing them -- the only departure signal that table has, since ZRRider.date_left
# lost its writer. Generous next to the sync's daily cadence, so an outage does not evict anyone.
_ZR_SEEN_DAYS = 30


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
        "last_requested_at": timezone.now(),
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


def mark_requested(zwids: list[int]) -> int:
    """Record that these riders were asked for, whether or not anything came back.

    This is the whole point of keeping ``last_requested_at`` separate. ``store_profiles``
    only touches rows it received data for, so a rider we ask about every cycle and the
    service has nothing for would look abandoned and eventually be evicted -- punishing them
    for a gap in the upstream data rather than for leaving the set we care about.

    Rows are only stamped if they already exist; there is nothing to record for a rider we
    have never successfully stored.

    Args:
        zwids: The riders included in the batch.

    Returns:
        How many existing rows were stamped.

    """
    if not zwids:
        return 0
    return RiderProfile.objects.filter(zwid__in=zwids).update(last_requested_at=timezone.now())


def zwids_to_refresh() -> list[int]:
    """Return the riders whose profiles we want kept current.

    Everyone who races for the team, which is the union of three sets:

    - **Members here** -- every registered user with a Zwift id, connected or not. A member who
      never linked Zwift still races and still needs a profile. Someone whose Discord
      membership has closed drops out, so leaving the team eventually releases their cached
      profile; an account with no guild row at all (a locally-created one, say) is kept.
    - **The ZwiftPower team page** -- riders who have not left it. Most never registered here,
      and for them this cache is the only place their racing data lives.
    - **The ZwiftRacing club** -- the same, for the riders in the club but not on that page.
      Judged by when the club sync last touched the row, not by ``ZRRider.date_left``: nothing
      has written that field since it was deliberately dropped from the club sync, so it would
      hold every rider who ever appeared in the club forever. The club sync rewrites each rider
      it still sees, so a row untouched for ``_ZR_SEEN_DAYS`` is one the club no longer lists.

    Riders connected to this app through zauth are deliberately absent: the service resolves
    them from ``connected_app``, so it holds that set rather than us keeping a copy.

    Dropping out of the request is what starts a rider's retention clock, since
    ``mark_requested`` stamps only the riders we asked about: the ZwiftPower team page stamps
    ``date_left`` when someone leaves, the club sync stops touching their row, and a member's
    guild membership closes. A rider still in any one of the three stays, which is the point --
    they still race for us.

    It is still narrower than "every rider the service knows about": the cache holds the team,
    not the platform.

    Returns:
        Distinct zwids, sorted.

    """
    from apps.zwiftpower.models import ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    members = (
        User.objects.filter(zwid__isnull=False, zwid__gt=0)
        .filter(Q(guild_member__isnull=True) | Q(guild_member__date_left__isnull=True))
        .values_list("zwid", flat=True)
    )
    zp_team = ZPTeamRiders.objects.filter(date_left__isnull=True, zwid__gt=0).values_list("zwid", flat=True)
    seen_since = timezone.now() - timedelta(days=_ZR_SEEN_DAYS)
    zr_club = ZRRider.objects.filter(
        date_left__isnull=True, zwid__gt=0, date_modified__gte=seen_since
    ).values_list("zwid", flat=True)
    return sorted({*members, *zp_team, *zr_club})


def last_successful_sync() -> datetime | None:
    """When ``sync_rider_profiles`` last finished successfully.

    Args:
        None.

    Returns:
        The finish time of the most recent successful run, or None if there has never been one.

    """
    from django.apps import apps as django_apps
    from django.db.models import Q

    try:
        results = django_apps.get_model("django_tasks_database", "DBTaskResult")
    except LookupError:  # pragma: no cover - the task backend is always installed in practice
        return None

    row = (
        results.objects.filter(
            Q(task_path__endswith="sync_rider_profiles") | Q(task_path="sync_rider_profiles"),
            status="SUCCESSFUL",
            finished_at__isnull=False,
        )
        .order_by("-finished_at")
        .values("finished_at")
        .first()
    )
    return row["finished_at"] if row else None


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
