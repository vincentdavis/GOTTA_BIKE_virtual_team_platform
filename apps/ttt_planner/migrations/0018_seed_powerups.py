"""Seed PowerUp rows from data/powerups.json, loading the scraped icons.

Icons live in the app's static dir (scraped from Zwift Insider); this migration
copies each into the PowerUp.icon ImageField (media storage). Idempotent: existing
rows (matched by slug) are left untouched.
"""

import json
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import migrations

_APP_DIR = Path(__file__).resolve().parent.parent
_DATA_FILE = _APP_DIR / "data" / "powerups.json"
_ICON_DIR = _APP_DIR / "static" / "ttt_planner" / "powerups"


def seed_powerups(apps, schema_editor):
    """Create PowerUp rows from the JSON seed and attach their icons."""
    PowerUp = apps.get_model("ttt_planner", "PowerUp")
    records = json.loads(_DATA_FILE.read_text())
    for order, rec in enumerate(records):
        slug = rec["slug"]
        if PowerUp.objects.filter(slug=slug).exists():
            continue
        powerup = PowerUp(
            name=rec["name"],
            aka=rec.get("aka", ""),
            slug=slug,
            effect=rec.get("effect", ""),
            duration_seconds=rec.get("duration_seconds", 0) or 0,
            event_only=bool(rec.get("event_only", False)),
            excluded_from_ladder=bool(rec.get("excluded_from_ladder", False)),
            order=order,
            is_active=True,
        )
        icon_name = rec.get("icon") or ""
        icon_path = _ICON_DIR / icon_name if icon_name else None
        if icon_path and icon_path.exists():
            powerup.icon.save(icon_name, ContentFile(icon_path.read_bytes()), save=False)
        powerup.save()


def unseed_powerups(apps, schema_editor):
    """Remove the seeded PowerUp rows (matched by slug)."""
    PowerUp = apps.get_model("ttt_planner", "PowerUp")
    slugs = [rec["slug"] for rec in json.loads(_DATA_FILE.read_text())]
    PowerUp.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):
    """Seed the PowerUps reference data."""

    dependencies = [
        ("ttt_planner", "0017_powerup"),
    ]

    operations = [
        migrations.RunPython(seed_powerups, unseed_powerups),
    ]
