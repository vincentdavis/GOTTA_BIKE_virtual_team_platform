"""Import / refresh Zwift segments (climbs, sprints) from the bundled dataset.

Thin wrapper over ``apps.ttt_planner.services.segment_import`` (shared with the
admin "Import segments" button). Segments are upserted on (name, world,
direction), so re-running is idempotent.

Usage::

    uv run python manage.py import_segments
    uv run python manage.py import_segments --file path/to/one.json
"""

from pathlib import Path
from typing import ClassVar

from django.core.management.base import BaseCommand

from apps.ttt_planner.services.segment_import import import_segments


class Command(BaseCommand):
    """Upsert Segment rows from the bundled segment dataset."""

    help: ClassVar[str] = "Import/refresh Zwift segments from the bundled dataset."

    def add_arguments(self, parser) -> None:
        """Register command arguments.

        Args:
            parser: The argument parser.

        """
        parser.add_argument("--file", default=None, help="Import a single JSON file instead of the whole dir")

    def handle(self, *args, **options) -> None:
        """Run the import.

        Args:
            args: Unused positional args.
            options: Parsed command options.

        """
        files = [Path(options["file"])] if options["file"] else None
        counts = import_segments(files=files)
        self.stdout.write(
            self.style.SUCCESS(
                f"Segments imported: {counts['created']} created, {counts['updated']} updated, "
                f"{counts['skipped']} skipped."
            )
        )
