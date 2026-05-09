"""App configuration for the user-facing API."""

from django.apps import AppConfig


class UserApiConfig(AppConfig):
    """Configuration for apps.user_api."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.user_api"
    verbose_name = "User API"
