"""Background tasks for the user-facing API app."""

from datetime import timedelta

import logfire
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.user_api.models import UserApiKey

PURGE_AGE_DAYS = 90


@task
def purge_expired_api_keys() -> dict:
    """Hard-delete API keys whose ``expires_at`` is more than 90 days in the past.

    Recently expired keys are kept around so users still see them in the
    management UI for a while; only ancient ones are pruned.

    Returns:
        Summary dict with status and the number of rows deleted.

    """
    cutoff = timezone.now() - timedelta(days=PURGE_AGE_DAYS)
    with logfire.span("purge_expired_api_keys", cutoff=cutoff.isoformat()):
        deleted, _ = UserApiKey.objects.filter(expires_at__lt=cutoff).delete()
        logfire.info("Expired API keys purged", deleted=deleted, cutoff=cutoff.isoformat())
        return {"status": "complete", "deleted": deleted, "cutoff": cutoff.isoformat()}
