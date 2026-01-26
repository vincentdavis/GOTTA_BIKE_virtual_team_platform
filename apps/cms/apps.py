"""App configuration for cms app."""

from django.apps import AppConfig


class CmsConfig(AppConfig):
    """Configuration for the CMS app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cms"
    verbose_name = "CMS"
