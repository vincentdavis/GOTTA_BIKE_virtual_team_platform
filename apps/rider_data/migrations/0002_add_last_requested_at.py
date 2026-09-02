"""Add last_requested_at and seed it from fetched_at."""

from django.db import migrations, models
from django.db.models import F


def backfill_last_requested(apps, schema_editor):
    """Seed ``last_requested_at`` from ``fetched_at`` for rows that predate the column.

    Without this, every existing row looks never-requested. The purge ignores nulls, so
    nothing would be wrongly deleted -- but those rows would also never become evictable,
    which is the opposite failure and just as quiet.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor (unused).

    """
    apps.get_model("rider_data", "RiderProfile").objects.update(last_requested_at=F("fetched_at"))


def unbackfill(apps, schema_editor):
    """Reverse no-op; the column itself is dropped by reversing the AddField.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor (unused).

    """


class Migration(migrations.Migration):
    """Add the request-time anchor used for retention."""

    dependencies = [
        ("rider_data", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="riderprofile",
            name="last_requested_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    "When this rider was last INCLUDED IN A BATCH, whether or not data came back. "
                    "Distinct from fetched_at on purpose: the difference is 'we asked and got "
                    "nothing' versus 'we stopped asking', and only the second is a reason to evict"
                ),
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="riderprofile",
            name="fetched_at",
            field=models.DateTimeField(db_index=True, help_text="When we last stored data for this rider"),
        ),
        migrations.RunPython(backfill_last_requested, unbackfill),
    ]
