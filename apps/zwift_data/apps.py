"""App config for the Zwift Speed Lab canonical dataset."""

from django.apps import AppConfig


class ZwiftDataConfig(AppConfig):
    """Canonical Zwift worlds / routes / segments sourced from Zwift Speed Lab."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.zwift_data"
    verbose_name = "Zwift Data (Speed Lab)"
