"""Hard-overwrite every event's squad_gender_options to the fixed [Male, Female, COED] set.

Squad gender options are no longer user-configurable; any custom per-event option lists from
before this change are replaced with the canonical three. Stray ``Squad.gender`` and
``EventSignup.signup_squad_gender`` values are intentionally left untouched (historical).
"""

from django.db import migrations

FIXED_OPTIONS = ["Male", "Female", "COED"]


def overwrite_options(apps, schema_editor):
    """Set squad_gender_options to the fixed list for every event."""
    Event = apps.get_model("events", "Event")
    Event.objects.exclude(squad_gender_options=FIXED_OPTIONS).update(squad_gender_options=FIXED_OPTIONS)


class Migration(migrations.Migration):
    """Standardize squad_gender_options across all events."""

    dependencies = [
        ("events", "0044_squad_enforce_gender_alter_squad_gender"),
    ]

    operations = [
        migrations.RunPython(overwrite_options, migrations.RunPython.noop),
    ]
