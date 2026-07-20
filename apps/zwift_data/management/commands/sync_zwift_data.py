"""Sync the Zwift Speed Lab dataset (worlds/routes/segments + profiles) synchronously.

Use for the initial seed and local development::

    uv run python manage.py sync_zwift_data
"""

from django.core.management.base import BaseCommand

from apps.zwift_data.services.sync import sync_dataset


class Command(BaseCommand):
    """Download the Speed Lab bundle and rebuild the canonical dataset."""

    help = "Download the Zwift Speed Lab bundle and refresh worlds/routes/segments + profiles."

    def handle(self, *args, **options) -> None:
        """Run the sync and report the counts written."""
        self.stdout.write("Downloading Zwift Speed Lab bundle…")
        result = sync_dataset()
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {result.worlds} worlds, {result.routes} routes, "
                f"{result.segments} segments, {result.profiles} profiles "
                f"({result.bundle_bytes / 1_000_000:.1f} MB bundle)."
            )
        )
