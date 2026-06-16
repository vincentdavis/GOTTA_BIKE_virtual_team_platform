"""Import / refresh Zwift TTT routes from the bundled dataset.

The dataset (``apps/ttt_planner/data/zwift_routes.json``) is sourced from the
MIT-licensed ``zwift-data`` package (github.com/andipaetzold/zwift-data). Re-run
this command after refreshing that file to pick up new routes.

Usage::

    uv run python manage.py import_routes
    uv run python manage.py import_routes --deactivate-missing
"""

import json
from pathlib import Path
from typing import ClassVar

from django.core.management.base import BaseCommand

from apps.ttt_planner.models import Route

DEFAULT_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "zwift_routes.json"


class Command(BaseCommand):
    """Upsert Route rows from the bundled Zwift route dataset."""

    help: ClassVar[str] = "Import/refresh Zwift TTT routes from the bundled dataset."

    def add_arguments(self, parser) -> None:
        """Register command arguments.

        Args:
            parser: The argument parser.

        """
        parser.add_argument("--file", default=str(DEFAULT_DATA_FILE), help="Path to the routes JSON file")
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="Set is_active=False on routes not present in the dataset",
        )

    def handle(self, *args, **options) -> None:
        """Run the import.

        Args:
            *args: Unused.
            **options: Parsed command options.

        """
        path = Path(options["file"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Dataset not found: {path}"))
            return

        rows = json.loads(path.read_text())
        created = updated = 0
        seen_ids: list[str] = []

        for row in rows:
            route_id = row.get("zwift_route_id") or ""
            name = row["name"]
            seen_ids.append(route_id)

            defaults = {
                "name": name,
                "world": row.get("world", ""),
                "distance_km": row["distance_km"],
                "elevation_m": row["elevation_m"],
                "zwift_route_id": route_id,
                "is_active": True,
            }

            # Match by zwift_route_id, else by name (reconciles older seeded rows
            # that have no zwift_route_id).
            obj = Route.objects.filter(zwift_route_id=route_id).first() if route_id else None
            if obj is None:
                obj = Route.objects.filter(zwift_route_id="", name__iexact=name).first()

            if obj is None:
                Route.objects.create(**defaults)
                created += 1
            else:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj.save()
                updated += 1

        if options["deactivate_missing"]:
            deactivated = Route.objects.exclude(zwift_route_id__in=[r for r in seen_ids if r]).update(is_active=False)
            self.stdout.write(f"Deactivated {deactivated} routes not in the dataset.")

        self.stdout.write(self.style.SUCCESS(f"Routes imported: {created} created, {updated} updated."))
