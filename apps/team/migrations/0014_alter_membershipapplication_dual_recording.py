"""Change MembershipApplication.dual_recording from CharField to BooleanField."""

from django.db import migrations, models


def clear_dual_recording_values(apps, schema_editor):
    """Set all dual_recording values to NULL before field type change.

    PostgreSQL cannot cast non-empty strings (e.g. "trainer") to boolean,
    and the CharField has a NOT NULL constraint that must be dropped first.
    SQLite doesn't need this step — Django recreates the table on AlterField.
    """
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE team_membershipapplication"
                " ALTER COLUMN dual_recording DROP NOT NULL;"
                " UPDATE team_membershipapplication SET dual_recording = NULL;"
            )


class Migration(migrations.Migration):
    """Change dual_recording from CharField to BooleanField(null=True)."""

    dependencies = [
        ("team", "0013_backfill_record_date"),
    ]

    operations = [
        migrations.RunPython(
            clear_dual_recording_values,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="membershipapplication",
            name="dual_recording",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Do you dual record data?",
                null=True,
            ),
        ),
    ]
