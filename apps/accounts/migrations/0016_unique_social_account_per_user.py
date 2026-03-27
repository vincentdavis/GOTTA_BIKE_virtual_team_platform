"""Add unique constraint: one SocialAccount per provider per user."""

from django.db import migrations


class Migration(migrations.Migration):
    """Prevent multiple SocialAccounts for the same user and provider."""

    dependencies = [
        ("accounts", "0015_alter_user_is_extra_verified"),
        ("socialaccount", "0006_alter_socialaccount_extra_data"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE UNIQUE INDEX unique_one_social_per_user ON socialaccount_socialaccount (user_id, provider);",
            reverse_sql="DROP INDEX IF EXISTS unique_one_social_per_user;",
        ),
    ]
