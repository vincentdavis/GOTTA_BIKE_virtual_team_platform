"""Admin configuration for gotta_bike_platform app."""

from django.contrib import admin

from gotta_bike_platform.models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Admin for SiteSettings singleton model."""

    list_display = ("__str__", "has_logo", "has_hero_image", "date_modified")
    readonly_fields = ("date_modified",)

    def has_logo(self, obj):
        """Check if logo is uploaded."""
        return bool(obj.site_logo)

    has_logo.boolean = True
    has_logo.short_description = "Logo"

    def has_hero_image(self, obj):
        """Check if hero image is uploaded."""
        return bool(obj.hero_image)

    has_hero_image.boolean = True
    has_hero_image.short_description = "Hero Image"

    def has_add_permission(self, request):
        """Prevent adding new instances if one exists."""
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of the singleton."""
        return False
