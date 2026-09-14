"""Models for gotta_bike_platform app."""

from django.core.cache import cache
from django.db import models

from gotta_bike_platform.retention import RetentionPolicy

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

    retention = RetentionPolicy.keep(
        "One row of site-wide images and display settings. Configuration."
    )

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
    # Age brackets are the one icon family that SHIPS a default set (see
    # apps/accounts/static/accounts/age/). An upload here replaces the bundled artwork for
    # that bracket; leaving it empty is the normal case, not a gap to be filled.
    age_jnr_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the Jnr age bracket. Leave empty to use the bundled default",
    )
    age_u23_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the U23 age bracket. Leave empty to use the bundled default",
    )
    age_snr_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the Snr age bracket. Leave empty to use the bundled default",
    )
    age_vet_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the Vet age bracket. Leave empty to use the bundled default",
    )
    age_mas_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the Mas age bracket. Leave empty to use the bundled default",
    )
    age_50plus_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the 50+ age bracket. Leave empty to use the bundled default",
    )
    age_60plus_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the 60+ age bracket. Leave empty to use the bundled default",
    )
    age_70plus_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for the 70+ age bracket. Leave empty to use the bundled default",
    )

    # One icon for "the kit is sorted" -- the two stored statuses that mean it (the team
    # completed the Zwift order, or the rider says it arrived) read the same from across a
    # roster, and the wording that separates them rides on the alt text. Ships a default,
    # like the age brackets, so the roster shows it without anyone uploading anything.
    kit_emoji = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for a rider who has the team kit. Leave empty to use the bundled default",
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
    new_member_icon = models.ImageField(
        upload_to="site/",
        null=True,
        blank=True,
        help_text="Icon for New Member status (recommended: 64x64 PNG)",
    )
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for SiteSettings model."""

        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self) -> str:
        """Return string representation.

        Returns:
            The human-readable name for the settings singleton.

        """
        return "Site Settings"

    def save(self, *args, **kwargs):
        """Ensure only one instance exists and invalidate cache."""
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(SITE_SETTINGS_CACHE_KEY)

    def delete(self, *args, **kwargs):
        """Prevent deletion of the singleton instance."""

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
