"""Background tasks for the Zwift Speed Lab dataset."""

from __future__ import annotations

import logfire
from django.tasks import task  # ty:ignore[unresolved-import]

from .services.sync import sync_dataset


@task()
def sync_zwift_data() -> dict[str, int]:
    """Download the Speed Lab bundle and refresh the canonical dataset.

    Returns:
        A dict of the counts written (worlds/routes/segments/profiles).

    """
    logfire.info("zwift_data sync task started")
    result = sync_dataset()
    return {
        "worlds": result.worlds,
        "routes": result.routes,
        "segments": result.segments,
        "profiles": result.profiles,
    }
