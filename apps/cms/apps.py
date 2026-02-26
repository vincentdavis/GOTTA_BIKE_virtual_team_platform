"""App configuration for cms app."""

from django.apps import AppConfig


class CmsConfig(AppConfig):
    """Configuration for the CMS app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cms"
    verbose_name = "CMS"

    def ready(self):
        """Connect signals to clear CMS nav cache on Page changes."""
        from django.db.models.signals import post_delete, post_save

        from apps.cms.context_processors import clear_cms_nav_cache
        from apps.cms.models import Page

        def _clear_nav_cache(sender, **kwargs):
            clear_cms_nav_cache()

        post_save.connect(_clear_nav_cache, sender=Page)
        post_delete.connect(_clear_nav_cache, sender=Page)
