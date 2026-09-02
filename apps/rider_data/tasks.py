"""Background tasks for rider_data."""

from datetime import timedelta

import logfire
from constance import config
from django.tasks import task
from django.utils import timezone

from apps.rider_data import client, services
from apps.rider_data.models import RiderProfile
from gotta_bike_platform.config import settings as app_config


@task
def sync_rider_profiles() -> dict:
    """Refresh cached rider profiles from zauth.

    Two populations, fetched together because they overlap and the service deduplicates by
    zwid anyway:

    * everyone registered here who has a Zwift id, which includes members who never linked
      Zwift and therefore never appear in the connected set;
    * everyone linked to this app, resolved by the service from ``connected_app`` so we do
      not have to keep a local copy of that list in step.

    Riders the service holds no data for are absent from the response rather than returned
    empty, so the stored count is legitimately lower than the requested count.

    This task deliberately does not touch ``zwid_verified`` or any other verification field.
    Connection status moves onto this source as its own step, because verification gates Race
    Verified status and Discord roles, and ``zwift_connection.status`` does not mean what its
    name suggests -- it reports whether a Zwift account exists service-wide, not whether the
    rider is still linked to us.

    Returns:
        Counts of rows fetched, created, updated and skipped.

    """
    if not client.is_configured():
        logfire.warning("Rider profile sync skipped: zauth service key not configured")
        return {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}

    with logfire.span("sync_rider_profiles"):
        requested = services.zwids_to_refresh()
        profiles = client.fetch_profiles(
            requested,
            connected_app=app_config.zwift_connected_app_name or None,
        )
        result = services.store_profiles(profiles)
        # Stamp everyone we asked about, including riders the service had nothing for.
        # Without this they drift toward eviction because of a gap in upstream data rather
        # than because they left the set we have a reason to hold.
        stamped = services.mark_requested(requested)
        return {"fetched": len(profiles), "requested": len(requested), "stamped": stamped, **result}


@task
def purge_rider_profiles() -> dict:
    """Delete cached profiles we have stopped asking about, once the window has passed.

    Anchored on ``last_requested_at`` -- when the rider was last INCLUDED IN A BATCH -- not on
    ``fetched_at``, when data last came back. The two diverge for a rider the service holds
    nothing for: we ask every cycle and store nothing every cycle, so ``fetched_at`` goes
    stale while we are still asking. Anchoring there would evict them for a gap in upstream
    data rather than for leaving the set, which is the one thing this sweep is meant to mean.

    That works because the sync is not demand-driven. It asks about a defined set on a
    schedule -- every registered user with a zwid, plus every rider linked to this app -- so a
    member is stamped every cycle whether or not anybody opens their profile. A stale
    ``last_requested_at`` therefore does not mean "nobody looked at them", it means "we have
    stopped having a reason to ask", which is the population worth evicting.

    Race activity was the obvious-looking anchor and is the wrong one. It describes the rider
    rather than our reason for holding them: someone who raced yesterday but has nothing to do
    with this team should go, while a member who has not raced in two years should stay. It is
    also derived from ZwiftPower results and excludes races with no club, so it is absent
    entirely for anyone racing unattached -- a deletion policy resting on it would look
    stricter than it is.

    Window is ``RIDER_PROFILE_MAX_DAYS``, 120 days by default; 0 disables the sweep, matching
    the convention the analytics and verification sweeps already use.

    Returns:
        Counts of rows ``considered`` and ``deleted``, and the cutoff applied.

    """
    max_days = config.RIDER_PROFILE_MAX_DAYS
    if not max_days or max_days <= 0:
        logfire.info("Rider profile retention disabled, skipping sweep")
        return {"considered": 0, "deleted": 0, "cutoff": None}

    # A broken sync looks exactly like every rider leaving at once: nothing gets stamped, so
    # every row ages past the window together. Without this the first purge after a long
    # outage would empty the cache, and the cause would be 120 days in the past.
    last_sync = services.last_successful_sync()
    if last_sync is None or timezone.now() - last_sync > timedelta(days=max_days):
        logfire.error(
            "Rider profile purge refused: no recent successful sync",
            last_successful_sync=last_sync.isoformat() if last_sync else None,
            max_days=max_days,
        )
        return {"considered": 0, "deleted": 0, "cutoff": None, "refused": "stale_sync"}

    cutoff = timezone.now() - timedelta(days=max_days)
    # Current members are never evicted. Ageing one out would only have the next sync
    # re-create the row, so this is churn prevention as much as anything -- and it is what
    # makes "removed if not a member" mean demotion rather than deletion: losing membership
    # does not delete the row, it stops protecting it.
    stale = RiderProfile.objects.filter(last_requested_at__lt=cutoff).exclude(
        zwid__in=services.protected_zwids()
    )
    considered = stale.count()
    total = RiderProfile.objects.count()

    # Second backstop, for the case the first cannot see: a sync that succeeds while asking
    # for the wrong set still stamps nobody. A single run should never remove most of the
    # cache, so an unusually large sweep is treated as a symptom rather than carried out.
    limit = config.RIDER_PROFILE_PURGE_MAX_FRACTION
    if total and limit and (considered / total) > limit:
        logfire.error(
            "Rider profile purge refused: would remove an implausible share of the cache",
            considered=considered,
            total=total,
            fraction=round(considered / total, 3),
            limit=limit,
        )
        return {"considered": considered, "deleted": 0, "cutoff": cutoff.isoformat(), "refused": "too_many"}

    deleted, _ = stale.delete()

    logfire.info(
        "Purged stale rider profiles",
        considered=considered,
        deleted=deleted,
        max_days=max_days,
        cutoff=cutoff.isoformat(),
    )
    return {"considered": considered, "deleted": deleted, "cutoff": cutoff.isoformat()}
