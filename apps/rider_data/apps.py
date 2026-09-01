"""App configuration for rider_data."""

from django.apps import AppConfig


class RiderDataConfig(AppConfig):
    """Configuration for the rider_data app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rider_data"
    verbose_name = "Rider Data"
