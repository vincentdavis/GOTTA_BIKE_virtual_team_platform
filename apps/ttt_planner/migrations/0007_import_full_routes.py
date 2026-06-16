"""Import the full Zwift route library and retire the small starter seed.

Loads ``apps/ttt_planner/data/zwift_routes.json`` (from the MIT-licensed
zwift-data package) so fresh deploys get the complete route list. Re-run the
``import_routes`` management command to refresh after updating that file.
"""

import json
from pathlib import Path

from django.db import migrations

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "zwift_routes.json"
# Starter-seed names that are superseded by canonical dataset routes.
OBSOLETE_SEED_NAMES = ("Flat Route", "Glasgow Worlds Course")


def import_routes(apps, schema_editor):
    """Upsert routes from the bundled dataset and drop obsolete seed rows."""
    route_model = apps.get_model("ttt_planner", "Route")
    rows = json.loads(DATA_FILE.read_text())

    for row in rows:
        route_id = row.get("zwift_route_id") or ""
        name = row["name"]
        defaults = {
            "name": name,
            "world": row.get("world", ""),
            "distance_km": row["distance_km"],
            "elevation_m": row["elevation_m"],
            "zwift_route_id": route_id,
            "is_active": True,
        }
        obj = route_model.objects.filter(zwift_route_id=route_id).first() if route_id else None
        if obj is None:
            obj = route_model.objects.filter(zwift_route_id="", name__iexact=name).first()
        if obj is None:
            route_model.objects.create(**defaults)
        else:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.save()

    route_model.objects.filter(zwift_route_id="", name__in=OBSOLETE_SEED_NAMES).delete()


def noop(apps, schema_editor):
    """Reverse is a no-op (we don't un-import the library)."""


class Migration(migrations.Migration):
    """Load the full route library."""

    dependencies = [
        ("ttt_planner", "0006_tttplan_zwiftgopher_error_and_more"),
    ]

    operations = [
        migrations.RunPython(import_routes, noop),
    ]
