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
            logfire.error("Strava sync failed", error=str(e))
            return {"error": str(e), "created": 0, "updated": 0, "errors": 1}
