"""Admin configuration for magic links."""

from typing import ClassVar

from django.contrib import admin

from apps.magic_links.models import MagicLink


@admin.register(MagicLink)
class MagicLinkAdmin(admin.ModelAdmin):
    """Admin for MagicLink model."""

    list_display: ClassVar[list[str]] = ["user", "redirect_url", "used", "date_created", "date_expires"]
    list_filter: ClassVar[list[str]] = ["used", "date_created"]
    search_fields: ClassVar[list[str]] = ["user__username", "user__email", "user__discord_username"]
    readonly_fields: ClassVar[list[str]] = ["token", "date_created", "date_expires"]
    raw_id_fields: ClassVar[list[str]] = ["user"]
