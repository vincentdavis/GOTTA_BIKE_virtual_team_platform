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
        profiles = client.fetch_profiles(
            services.zwids_to_refresh(),
            connected_app=app_config.zwift_connected_app_name or None,
        )
        result = services.store_profiles(profiles)
        return {"fetched": len(profiles), **result}


@task
def purge_rider_profiles() -> dict:
    """Delete cached profiles for riders who have not raced inside the retention window.

    Anchored on ``last_race_at`` rather than ``fetched_at``, which is the distinction that
    makes this safe: fetch time says when we last looked, not whether the rider still
    matters. Ageing on it would evict an active teammate simply because nothing had opened
    their profile lately, and the next sync would immediately re-create the row -- churn that
    protects nobody.

    A profile with no known race is kept. That sounds backwards for a retention sweep, but
    ``last_race_at`` is derived from ZwiftPower results, so a null means "we have no race
    history for them", not "they are inactive" -- and deleting on absence of evidence would
    remove the riders we know least about, which is the wrong direction.

    Zero disables the sweep, matching the convention the analytics and verification sweeps
    already use.

    Returns:
        Counts of rows ``considered`` and ``deleted``, and the cutoff applied.

    """
    max_days = config.RIDER_PROFILE_MAX_DAYS
    if not max_days or max_days <= 0:
        logfire.info("Rider profile retention disabled, skipping sweep")
        return {"considered": 0, "deleted": 0, "cutoff": None}

    cutoff = timezone.now() - timedelta(days=max_days)
    # Current members are never evicted, whatever their race activity. Ageing one out would
    # only have the next sync re-create the row, so this is churn prevention as much as
    # anything -- and it is what makes "removed if not a member" mean demotion rather than
    # deletion: losing membership does not delete the row, it stops protecting it.
    stale = RiderProfile.objects.filter(last_race_at__isnull=False, last_race_at__lt=cutoff).exclude(
        zwid__in=services.protected_zwids()
    )
    considered = stale.count()
    deleted, _ = stale.delete()

    logfire.info(
        "Purged stale rider profiles",
        considered=considered,
        deleted=deleted,
        max_days=max_days,
        cutoff=cutoff.isoformat(),
    )
    return {"considered": considered, "deleted": deleted, "cutoff": cutoff.isoformat()}
