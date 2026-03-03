"""Rename EventRegistration to RaceRegistration."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0023_add_availability_questions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name="EventRegistration",
            new_name="RaceRegistration",
        ),
        migrations.AlterModelOptions(
            name="raceregistration",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Race Registration",
                "verbose_name_plural": "Race Registrations",
            },
        ),
        migrations.AlterField(
            model_name="raceregistration",
            name="user",
            field=models.ForeignKey(
                help_text="The registered user",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="race_registrations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
