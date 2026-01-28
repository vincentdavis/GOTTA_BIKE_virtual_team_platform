"""Admin configuration for CMS app."""

from typing import ClassVar

from django.contrib import admin
from django.utils.html import format_html

from apps.cms.models import Page


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    """Admin configuration for Page model."""

    list_display: ClassVar[list[str]] = [
        "title",
        "slug",
        "status_badge",
        "show_in_nav",
        "nav_order",
        "require_login",
        "require_team_member",
        "updated_at",
    ]
    list_filter: ClassVar[list[str]] = [
        "status",
        "show_in_nav",
        "require_login",
        "require_team_member",
    ]
    search_fields: ClassVar[list[str]] = ["title", "slug", "content"]
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("title",)}
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at", "created_by"]

    fieldsets = (
        (
            None,
            {
                "fields": ("title", "slug", "status"),
            },
        ),
        (
            "Content",
            {
                "fields": ("content",),
                "description": "Main page content in Markdown format.",
            },
        ),
        (
            "Hero Section",
            {
                "fields": ("hero_enabled", "hero_image", "hero_title", "hero_subtitle"),
                "classes": ("collapse",),
                "description": "Optional hero section at the top of the page.",
            },
        ),
        (
            "Card Sections",
            {
                "fields": ("cards_above", "cards_below"),
                "classes": ("collapse",),
                "description": (
                    'JSON arrays of card objects. Format: [{"icon": "emoji", "title": "...", '
                    '"description": "...", "link_url": "/path/", "link_text": "...", "link_new_tab": true}]'
                ),
            },
        ),
        (
            "Navigation",
            {
                "fields": ("show_in_nav", "nav_title", "nav_order"),
                "description": "Control how this page appears in the sidebar navigation.",
            },
        ),
        (
            "Access Control",
            {
                "fields": ("require_login", "require_team_member"),
                "description": "Control who can view this page.",
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_at", "updated_at", "created_by"),
                "classes": ("collapse",),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        """Set created_by on first save."""
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Status")
    def status_badge(self, obj) -> str:
        """Display status as a colored badge.

        Args:
            obj: The Page object.

        Returns:
            HTML formatted status badge.

        """
        if obj.status == Page.Status.PUBLISHED:
            return format_html('<span style="color: green; font-weight: bold;">Published</span>')
        return format_html('<span style="color: orange; font-weight: bold;">Draft</span>')
