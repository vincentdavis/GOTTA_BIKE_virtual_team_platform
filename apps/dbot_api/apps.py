"""Django app configuration for dbot_api."""

from django.apps import AppConfig


class DbotApiConfig(AppConfig):
    """App configuration for dbot_api."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dbot_api"
    verbose_name = "Discord Bot API"
