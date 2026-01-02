"""App configuration for data_connection."""

from django.apps import AppConfig


class DataConnectionConfig(AppConfig):
    """Configuration for the data_connection app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.data_connection"
    verbose_name = "Data Connection"
