"""Change MembershipApplication.dual_recording from CharField to BooleanField."""

from django.db import migrations, models


def clear_dual_recording_values(apps, schema_editor):
    """Set all dual_recording values to empty string before field type change."""
    MembershipApplication = apps.get_model("team", "MembershipApplication")
    MembershipApplication.objects.all().update(dual_recording="")


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
