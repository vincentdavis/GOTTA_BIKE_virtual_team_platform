"""App configuration for the team app."""

from django.apps import AppConfig


class TeamConfig(AppConfig):
    """Django app configuration for the team app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.team'
