"""Admin configuration for analytics app."""

from django.contrib import admin

from apps.analytics.models import PageVisit


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    """Admin interface for PageVisit model."""

    list_display = (
        "timestamp",
        "path",
        "user",
        "device_type",
        "browser",
        "os",
        "ip_address",
    )
    list_filter = (
        "device_type",
        "browser",
        "os",
        "timestamp",
    )
    search_fields = (
        "path",
        "user__username",
        "user__discord_username",
        "ip_address",
    )
    readonly_fields = (
        "timestamp",
        "user",
        "ip_address",
        "user_agent",
        "path",
        "referer",
        "screen_width",
        "screen_height",
        "viewport_width",
        "timezone",
        "browser",
        "browser_version",
        "os",
        "device_type",
    )
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)

    def has_add_permission(self, request) -> bool:
        """Disable manual creation of page visits.

        Returns:
            False - visits are only created via JavaScript tracking.

        """
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """Disable editing of page visits.

        Returns:
            False - visits are read-only records.

        """
        return False
