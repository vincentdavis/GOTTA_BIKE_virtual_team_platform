"""App configuration for zwift."""

from django.apps import AppConfig


class ZwiftConfig(AppConfig):
    """Configuration for the zwift app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.zwift"
    verbose_name = "Zwift"
