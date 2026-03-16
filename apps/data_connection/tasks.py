"""Background tasks for data_connection app."""

import logfire
from django.tasks import task

from apps.data_connection.models import DataConnection
from apps.data_connection.services import sync_connection


@task()
def sync_all_data_connections() -> None:
    """Sync all data connections that have auto_sync enabled.

    Skips expired and broken connections. Logs success/failure for each.
    """
    connections = DataConnection.objects.filter(auto_sync=True)
    synced = 0
    skipped = 0
    failed = 0

    for connection in connections:
        if connection.is_expired:
            logfire.debug("Skipping expired data connection", connection_id=connection.id, title=connection.title)
            skipped += 1
            continue
        if connection.is_broken:
            logfire.debug("Skipping broken data connection", connection_id=connection.id, title=connection.title)
            skipped += 1
            continue

        try:
            row_count = sync_connection(connection)
            logfire.info(
                "Auto-synced data connection",
                connection_id=connection.id,
                title=connection.title,
                row_count=row_count,
            )
            synced += 1
        except Exception as e:
            logfire.error(
                "Failed to auto-sync data connection",
                connection_id=connection.id,
                title=connection.title,
                error=str(e),
            )
            failed += 1

    logfire.info(
        "Data connection auto-sync complete",
        synced=synced,
        skipped=skipped,
        failed=failed,
    )
