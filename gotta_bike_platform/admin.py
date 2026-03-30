"""Admin configuration for gotta_bike_platform app."""

from django.contrib import admin

from django_tasks_db.admin import DBTaskResultAdmin as DefaultDBTaskResultAdmin
from django_tasks_db.models import DBTaskResult

from gotta_bike_platform.models import SiteSettings


admin.site.unregister(DBTaskResult)


@admin.register(DBTaskResult)
class DBTaskResultAdmin(DefaultDBTaskResultAdmin):
    """Extend default task admin with search by task path."""

    search_fields = ("task_path",)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Admin for SiteSettings singleton model."""

    list_display = ("__str__", "has_logo", "has_hero_image", "has_verification_emojis", "date_modified")
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

    def has_verification_emojis(self, obj):
        """Check if any verification emoji is uploaded."""
        return bool(obj.not_verified_emoji or obj.verified_emoji or obj.extra_verified_emoji)

    has_verification_emojis.boolean = True
    has_verification_emojis.short_description = "Verification Emojis"

    def has_add_permission(self, request):
        """Prevent adding new instances if one exists."""
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of the singleton."""
        return False
