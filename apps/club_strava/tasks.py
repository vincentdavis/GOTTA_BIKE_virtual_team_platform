"""Background tasks for Strava club activities.

Uses Django 6.0 background tasks feature with django-tasks database backend.
"""

import logfire
from django.tasks import task

from apps.club_strava.strava_client import sync_club_activities


@task
def sync_strava_activities() -> dict:
    """Fetch club activities from Strava and update the database.

    Returns:
        dict with counts of created, updated, and errors.

    """
    with logfire.span("sync_strava_activities"):
        logfire.info("Starting Strava club activities sync")

        try:
            results = sync_club_activities(pages=2)
            logfire.info(
                "Strava sync complete",
                created=results["created"],
                updated=results["updated"],
                errors=results["errors"],
            )
            return results
        except Exception as e:
            import traceback

            logfire.error(
                "Strava sync failed",
                error=str(e),
                error_type=type(e).__name__,
                traceback=traceback.format_exc(),
            )
            return {"error": str(e), "created": 0, "updated": 0, "errors": 1}


@task
def purge_strava_activities() -> dict:
    """Delete club activities older than the retention window.

    Anchored on ``date_created`` -- when we ingested the row -- rather than ``activity_date``,
    which sounds like the natural choice and does not work. Strava's club-activities feed
    returns a reduced payload that omits the start date entirely, the same privacy reduction
    that strips the athlete ID, so ``activity_date`` is null on every row we hold. A sweep
    anchored on it would match nothing and look like a working policy.

    That missing athlete ID is also why this window matters more than most. Without it we
    cannot find one person's rows, so we could not honour an erasure request against this
    table even if asked -- retention is the only control there is.

    ``STRAVA_ACTIVITY_MAX_DAYS`` sets the window, 120 days by default; 0 disables the sweep,
    matching the convention the other retention tasks use.

    Returns:
        Counts of rows ``considered`` and ``deleted``, and the cutoff applied.

    """
    from datetime import timedelta

    from constance import config
    from django.utils import timezone

    from apps.club_strava.models import ClubActivity

    max_days = config.STRAVA_ACTIVITY_MAX_DAYS
    if not max_days or max_days <= 0:
        logfire.info("Strava activity retention disabled, skipping sweep")
        return {"considered": 0, "deleted": 0, "cutoff": None}

    cutoff = timezone.now() - timedelta(days=max_days)
    with logfire.span("purge_strava_activities"):
        stale = ClubActivity.objects.filter(date_created__lt=cutoff)
        considered = stale.count()
        deleted, _ = stale.delete()

        logfire.info(
            "Purged aged Strava club activities",
            considered=considered,
            deleted=deleted,
            max_days=max_days,
            cutoff=cutoff.isoformat(),
        )
    return {"considered": considered, "deleted": deleted, "cutoff": cutoff.isoformat()}
