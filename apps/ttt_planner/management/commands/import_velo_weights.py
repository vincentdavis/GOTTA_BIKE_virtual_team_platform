"""Import ZwiftRacing vELO2 "Race" factor weights onto Route rows.

Parses the ZwiftRacing routes reference table (``apps/zwiftracing/docs/ZwiftRacing
Routes VELO WEIGHTS.md``) and writes the five Race-rating factor weights (Sprint,
Punch, Climb, Endurance, Pursuit) onto matching ``Route`` rows, matched by name
(case-insensitive). Re-run after refreshing the source doc.

Usage::

    uv run python manage.py import_velo_weights
    uv run python manage.py import_velo_weights --file "path/to/weights.md"
"""

import re
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

from django.core.management.base import BaseCommand

from apps.ttt_planner.models import Route

DEFAULT_DATA_FILE = (
    Path(__file__).resolve().parents[3] / "zwiftracing" / "docs" / "ZwiftRacing Routes VELO WEIGHTS.md"
)

# | World | [Name](url) | Sprint% | Punch% | Climb% | Endurance% | Pursuit% |
ROW_RE = re.compile(
    r"^\|\s*(?P<world>[^|]+?)\s*\|\s*\[(?P<name>[^\]]+)\]\([^)]*\)\s*\|"
    r"\s*(?P<sprint>[\d.]+)%\s*\|\s*(?P<punch>[\d.]+)%\s*\|\s*(?P<climb>[\d.]+)%\s*\|"
    r"\s*(?P<endurance>[\d.]+)%\s*\|\s*(?P<pursuit>[\d.]+)%\s*\|",
)


class Command(BaseCommand):
    """Write vELO2 Race factor weights onto Route rows from the reference doc."""

    help: ClassVar[str] = "Import ZwiftRacing vELO2 Race factor weights onto routes (matched by name)."

    def add_arguments(self, parser) -> None:
        """Register command arguments.

        Args:
            parser: The argument parser.

        """
        parser.add_argument("--file", default=str(DEFAULT_DATA_FILE), help="Path to the vELO weights markdown file")
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="No-op if any route already has weights (safe to run on every deploy; won't clobber manual edits)",
        )

    def handle(self, *args, **options) -> None:
        """Run the import.

        Args:
            *args: Unused.
            **options: Parsed command options.

        """
        if options["if_empty"] and Route.objects.filter(velo_sprint__isnull=False).exists():
            self.stdout.write("Routes already have vELO2 weights — skipping (--if-empty).")
            return

        path = Path(options["file"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Weights file not found: {path}"))
            return

        rows = [m.groupdict() for line in path.read_text().splitlines() if (m := ROW_RE.match(line))]
        if not rows:
            self.stderr.write(self.style.ERROR("No route rows parsed — is the file a markdown table?"))
            return

        updated = 0
        unmatched: list[str] = []
        for row in rows:
            route = Route.objects.filter(name__iexact=row["name"].strip()).first()
            if route is None:
                unmatched.append(f"{row['world'].strip()} / {row['name'].strip()}")
                continue
            route.velo_sprint = Decimal(row["sprint"])
            route.velo_punch = Decimal(row["punch"])
            route.velo_climb = Decimal(row["climb"])
            route.velo_endurance = Decimal(row["endurance"])
            route.velo_pursuit = Decimal(row["pursuit"])
            route.save(
                update_fields=["velo_sprint", "velo_punch", "velo_climb", "velo_endurance", "velo_pursuit"]
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"vELO2 weights imported: {updated}/{len(rows)} rows matched a route."))
        if unmatched:
            self.stdout.write(self.style.WARNING(f"{len(unmatched)} weight rows had no matching route:"))
            for label in unmatched:
                self.stdout.write(f"  - {label}")

        without = Route.objects.filter(velo_sprint__isnull=True).count()
        if without:
            self.stdout.write(self.style.WARNING(f"{without} routes still have no vELO2 weights."))
