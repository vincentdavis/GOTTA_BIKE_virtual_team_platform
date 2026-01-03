"""Add permission_overrides field to User model."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add permission_overrides field for manual permission grants/revokes."""

    dependencies = [
        ("accounts", "0009_add_guild_member"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="permission_overrides",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Manual permission overrides: {permission_name: True/False}. True grants, False revokes.",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="roles",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of user roles (legacy - use permission_overrides for new grants)",
            ),
        ),
    ]
