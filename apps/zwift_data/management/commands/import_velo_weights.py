"""Import ZwiftRacing vELO2 Race factor weights onto canonical routes (by name_hash).

Usage::

    uv run python manage.py import_velo_weights
    uv run python manage.py import_velo_weights --file path/to/weights.json
    uv run python manage.py import_velo_weights --if-empty   # deploy: no-op once loaded
"""

from django.core.management.base import BaseCommand

from apps.zwift_data.models import ZwiftRoute
from apps.zwift_data.services.velo import DEFAULT_VELO_FILE, import_velo_from_file


class Command(BaseCommand):
    """Load vELO2 Race weights from the ZwiftRacing routes JSON onto ZwiftRoute rows."""

    help = "Import ZwiftRacing vELO2 Race factor weights onto canonical routes (matched by name_hash)."

    def add_arguments(self, parser) -> None:
        """Register command arguments.

        Args:
            parser: The argument parser.

        """
        parser.add_argument("--file", default=str(DEFAULT_VELO_FILE), help="Path to the vELO weights JSON file")
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="No-op if any route already has weights (safe to run on every deploy; won't clobber edits)",
        )

    def handle(self, *args, **options) -> None:
        """Run the import and report the counts.

        Args:
            *args: Unused.
            **options: Parsed command options.

        """
        if options["if_empty"] and ZwiftRoute.objects.exclude(velo_sprint__isnull=True).exists():
            self.stdout.write("vELO weights already present — skipping (--if-empty).")
            return
        result = import_velo_from_file(options["file"])
        self.stdout.write(
            self.style.SUCCESS(
                f"vELO weights: {result.updated} routes updated, {result.unmatched} unmatched "
                f"of {result.total} entries."
            )
        )
        if result.unmatched_names:
            self.stdout.write("Unmatched: " + ", ".join(result.unmatched_names[:20]))
