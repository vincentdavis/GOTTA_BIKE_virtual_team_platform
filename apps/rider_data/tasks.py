"""Background tasks for rider_data."""

from datetime import timedelta

import logfire
from constance import config
from django.tasks import task
from django.utils import timezone

from apps.rider_data.models import RiderProfile


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
    stale = RiderProfile.objects.filter(last_race_at__isnull=False, last_race_at__lt=cutoff)
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
