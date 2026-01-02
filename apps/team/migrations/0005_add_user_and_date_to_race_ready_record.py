"""Add user and date_created to RaceReadyRecord."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    """Add user ForeignKey and date_created to RaceReadyRecord."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("team", "0004_teamconfig_height_verification_and_more"),
    ]

    operations = [
        # First add user as nullable, then we'll make it required
        migrations.AddField(
            model_name="racereadyrecord",
            name="user",
            field=models.ForeignKey(
                help_text="User this record belongs to",
                null=True,  # Temporarily nullable
                on_delete=django.db.models.deletion.CASCADE,
                related_name="race_ready_records",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="racereadyrecord",
            name="date_created",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                help_text="When this record was created",
            ),
        ),
        # Delete any orphaned records (records without a user)
        migrations.RunSQL(
            sql="DELETE FROM team_racereadyrecord WHERE user_id IS NULL;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Now make user non-nullable
        migrations.AlterField(
            model_name="racereadyrecord",
            name="user",
            field=models.ForeignKey(
                help_text="User this record belongs to",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="race_ready_records",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Update ordering
        migrations.AlterModelOptions(
            name="racereadyrecord",
            options={
                "ordering": ["-date_created"],
                "verbose_name": "Race Ready Record",
                "verbose_name_plural": "Race Ready Records",
            },
        ),
    ]
