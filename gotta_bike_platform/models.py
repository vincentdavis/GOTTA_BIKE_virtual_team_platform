"""Models for gotta_bike_platform app."""

from django.core.cache import cache
from django.db import models

SITE_SETTINGS_CACHE_KEY = "site_settings_singleton"
SITE_SETTINGS_CACHE_TIMEOUT = 300  # 5 minutes


class SiteSettings(models.Model):
    """Singleton model for site-wide image settings.

    This model stores uploaded images for the site logo, favicon, hero section,
    and verification status emojis.
    Only one instance should exist - use SiteSettings.get_settings() to access.

    Attributes:
        site_logo: Uploaded logo image for the header.
        favicon: Uploaded favicon for browser tabs.
        hero_image: Uploaded image for the home page hero section.
        not_verified_emoji: Emoji/icon for not-verified status.
        verified_emoji: Emoji/icon for verified status.
        extra_verified_emoji: Emoji/icon for extra-verified status.
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
    not_verified_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon shown for not-verified status (recommended: 64x64 PNG)",
    )
    verified_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon shown for verified status (recommended: 64x64 PNG)",
    )
    extra_verified_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon shown for extra-verified status (recommended: 64x64 PNG)",
    )
    zp_a_plus_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for ZwiftPower A+ category (recommended: 64x64 PNG)",
    )
    zp_a_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for ZwiftPower A category (recommended: 64x64 PNG)",
    )
    zp_b_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for ZwiftPower B category (recommended: 64x64 PNG)",
    )
    zp_c_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for ZwiftPower C category (recommended: 64x64 PNG)",
    )
    zp_d_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for ZwiftPower D category (recommended: 64x64 PNG)",
    )
    zp_e_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for ZwiftPower E category (recommended: 64x64 PNG)",
    )
    zr_diamond_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Diamond category (recommended: 64x64 PNG)",
    )
    zr_ruby_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Ruby category (recommended: 64x64 PNG)",
    )
    zr_emerald_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Emerald category (recommended: 64x64 PNG)",
    )
    zr_sapphire_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Sapphire category (recommended: 64x64 PNG)",
    )
    zr_amethyst_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Amethyst category (recommended: 64x64 PNG)",
    )
    zr_platinum_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Platinum category (recommended: 64x64 PNG)",
    )
    zr_gold_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Gold category (recommended: 64x64 PNG)",
    )
    zr_silver_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Silver category (recommended: 64x64 PNG)",
    )
    zr_bronze_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Bronze category (recommended: 64x64 PNG)",
    )
    zr_copper_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Zwift Racing Copper category (recommended: 64x64 PNG)",
    )
    phenotype_allrounder_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for All-Rounder phenotype (recommended: 64x64 PNG)",
    )
    phenotype_climber_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Climber phenotype (recommended: 64x64 PNG)",
    )
    phenotype_puncheur_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Puncheur phenotype (recommended: 64x64 PNG)",
    )
    phenotype_tt_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Time Trialist phenotype (recommended: 64x64 PNG)",
    )
    phenotype_sprinter_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Sprinter phenotype (recommended: 64x64 PNG)",
    )
    phenotype_pursuiter_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Emoji/icon for Pursuiter phenotype (recommended: 64x64 PNG)",
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
        """Ensure only one instance exists and invalidate cache."""
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(SITE_SETTINGS_CACHE_KEY)

    def delete(self, *args, **kwargs):
        """Prevent deletion of the singleton instance."""
        pass

    @classmethod
    def get_settings(cls) -> SiteSettings:
        """Get or create the singleton settings instance (cached).

        Returns:
            The SiteSettings instance.

        """
        obj = cache.get(SITE_SETTINGS_CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(SITE_SETTINGS_CACHE_KEY, obj, SITE_SETTINGS_CACHE_TIMEOUT)
        return obj
