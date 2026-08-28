"""Background tasks for the analytics app."""

from datetime import timedelta

import logfire
from constance import config
from django.tasks import task
from django.utils import timezone

from apps.analytics.models import PageVisit


@task
def purge_page_visits() -> dict:
    """Age out page-visit rows in two stages: anonymise, then delete.

    No aggregated copy of this data exists -- every figure on the analytics dashboard is
    computed live from these rows -- so deleting them outright would throw away the
    history along with the personal data. Anonymising first keeps what the dashboard is
    actually for: which pages, which browsers, which devices, which timezones, over time.

    What goes at stage one is what identifies a person: the IP address, the link to their
    account, and the user-agent string (far more distinguishing than the parsed browser and
    OS fields kept beside it). What stays cannot single anyone out.

    One metric does suffer. ``unique_visitors`` counts distinct IPs, so it is unavailable
    for anonymised periods. Storing a salted hash instead of the address would preserve it
    without keeping the address, which is the better long-term answer but needs a column.

    Returns:
        Counts of rows ``anonymised`` and ``deleted``, with the cutoffs used.

    """
    now = timezone.now()
    anonymise_after = int(config.ANALYTICS_ANONYMISE_DAYS or 0)
    delete_after = int(config.ANALYTICS_DELETE_DAYS or 0)

    with logfire.span("purge_page_visits"):
        deleted = 0
        if delete_after > 0:
            delete_cutoff = now - timedelta(days=delete_after)
            deleted, _ = PageVisit.objects.filter(timestamp__lt=delete_cutoff).delete()

        anonymised = 0
        if anonymise_after > 0:
            anonymise_cutoff = now - timedelta(days=anonymise_after)
            # Only rows still carrying something identifying, so a re-run is a no-op
            # rather than rewriting the whole tail of the table every time.
            stale = PageVisit.objects.filter(timestamp__lt=anonymise_cutoff).exclude(
                ip_address__isnull=True, user__isnull=True, user_agent=""
            )
            anonymised = stale.update(ip_address=None, user=None, user_agent="")

        logfire.info(
            "Page visits aged out",
            anonymised=anonymised,
            deleted=deleted,
            anonymise_after_days=anonymise_after,
            delete_after_days=delete_after,
        )
        return {
            "status": "complete",
            "anonymised": anonymised,
            "deleted": deleted,
        }
