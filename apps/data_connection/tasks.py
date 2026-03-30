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
    with logfire.span("sync_all_data_connections"):
        all_connections = DataConnection.objects.all()
        connections = all_connections.filter(auto_sync=True)
        total_count = all_connections.count()
        auto_sync_count = connections.count()
        logfire.info(
            "Starting data connection sync",
            total_connections=total_count,
            auto_sync_connections=auto_sync_count,
        )

        if auto_sync_count == 0:
            logfire.warning("No data connections have auto_sync enabled")
            return

        synced = 0
        skipped = 0
        failed = 0

        for connection in connections:
            if connection.is_expired:
                logfire.info(
                    "Skipping expired data connection",
                    connection_id=connection.id,
                    title=connection.title,
                    date_expires=str(connection.date_expires),
                )
                skipped += 1
                continue
            if connection.is_broken:
                logfire.info(
                    "Skipping broken data connection",
                    connection_id=connection.id,
                    title=connection.title,
                )
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
