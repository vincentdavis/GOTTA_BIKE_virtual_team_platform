"""App configuration for the tickets app."""

from django.apps import AppConfig


class TicketsConfig(AppConfig):
    """Django app configuration for tickets."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tickets"
