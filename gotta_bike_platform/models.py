"""Models for gotta_bike_platform app."""

from django.db import models


class SiteSettings(models.Model):
    """Singleton model for site-wide image settings.

    This model stores uploaded images for the site logo, favicon, and hero section.
    Only one instance should exist - use SiteSettings.get_settings() to access.

    Attributes:
        site_logo: Uploaded logo image for the header.
        favicon: Uploaded favicon for browser tabs.
        hero_image: Uploaded image for the home page hero section.
        date_modified: When the settings were last updated.

    """

    site_logo = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Site logo displayed in the header (recommended: 200x50 PNG with transparency)",
    )
    favicon = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Favicon for browser tabs (recommended: 32x32 or 64x64 PNG)",
    )
    hero_image = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Hero image for the home page (recommended: 1920x600)",
    )
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for SiteSettings model."""

        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self) -> str:
        """Return string representation."""
        return "Site Settings"

    def save(self, *args, **kwargs):
        """Ensure only one instance exists."""
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of the singleton instance."""
        pass

    @classmethod
    def get_settings(cls) -> "SiteSettings":
        """Get or create the singleton settings instance.

        Returns:
            The SiteSettings instance.

        """
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
