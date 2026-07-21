"""Sync the Zwift Speed Lab dataset (worlds/routes/segments + profiles) synchronously.

Use for the initial seed and local development::

    uv run python manage.py sync_zwift_data
"""

from django.core.management.base import BaseCommand

from apps.zwift_data.models import ZwiftRoute
from apps.zwift_data.services.sync import sync_dataset


class Command(BaseCommand):
    """Download the Speed Lab bundle and rebuild the canonical dataset."""

    help = "Download the Zwift Speed Lab bundle and refresh worlds/routes/segments + profiles."

    def add_arguments(self, parser) -> None:
        """Register command arguments.

        Args:
            parser: The argument parser.

        """
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="No-op if the dataset already has routes (safe to run on every deploy for first-time seeding)",
        )

    def handle(self, *args, **options) -> None:
        """Run the sync and report the counts written."""
        if options["if_empty"] and ZwiftRoute.objects.exists():
            self.stdout.write("Zwift dataset already present — skipping (--if-empty).")
            return
        self.stdout.write("Downloading Zwift Speed Lab bundle…")
        result = sync_dataset()
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {result.worlds} worlds, {result.routes} routes, "
                f"{result.segments} segments, {result.profiles} profiles "
                f"({result.bundle_bytes / 1_000_000:.1f} MB bundle)."
            )
        )
