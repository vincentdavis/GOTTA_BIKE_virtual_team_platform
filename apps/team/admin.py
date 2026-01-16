"""Admin configuration for team app."""

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path

from apps.team.models import DiscordRole, MembershipApplication, RaceReadyRecord, TeamLink
from apps.team.services import get_unified_team_roster


@admin.register(RaceReadyRecord)
class RaceReadyRecordAdmin(admin.ModelAdmin):
    """Admin for RaceReadyRecord model."""

    list_display = ("user", "verify_type", "media_type", "date_created")
    list_filter = ("verify_type", "media_type", "date_created")
    search_fields = ("user__username", "user__email", "user__discord_username", "notes")
    raw_id_fields = ("user",)
    readonly_fields = ("date_created",)

    def get_urls(self) -> list:
        """Add custom URLs for unified roster view.

        Returns:
            List of URL patterns including custom roster URL.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "unified-roster/",
                self.admin_site.admin_view(self.unified_roster_view),
                name="team_unified_roster",
            ),
        ]
        return custom_urls + urls

    def unified_roster_view(self, request: HttpRequest) -> HttpResponse:
        """Display unified team roster.

        Args:
            request: The HTTP request.

        Returns:
            Rendered roster page.

        """
        roster = get_unified_team_roster()

        # Optional filtering
        filter_status = request.GET.get("status", "all")
        if filter_status == "active":
            roster = [r for r in roster if r.is_active_member]
        elif filter_status == "has_account":
            roster = [r for r in roster if r.has_account]
        elif filter_status == "no_account":
            roster = [r for r in roster if not r.has_account]

        context = {
            **self.admin_site.each_context(request),
            "title": "Unified Team Roster",
            "roster": roster,
            "filter_status": filter_status,
            "total_count": len(roster),
            "active_count": sum(1 for r in get_unified_team_roster() if r.is_active_member),
            "with_account": sum(1 for r in get_unified_team_roster() if r.has_account),
        }
        return render(request, "admin/team/unified_roster.html", context)


@admin.register(TeamLink)
class TeamLinkAdmin(admin.ModelAdmin):
    """Admin for TeamLink model."""

    list_display = (
        "title",
        "link_types_display",
        "active",
        "is_visible_display",
        "date_open",
        "date_closed",
        "date_added",
    )
    list_filter = ("active", "date_open", "date_closed")
    search_fields = ("title", "description", "url")
    readonly_fields = ("date_added", "date_edited")
    list_editable = ("active",)
    fieldsets = (
        (None, {"fields": ("title", "description", "url", "link_types", "active")}),
        ("Visibility Schedule", {"fields": ("date_open", "date_closed")}),
        ("Timestamps", {"fields": ("date_added", "date_edited"), "classes": ("collapse",)}),
    )

    @admin.display(description="Types")
    def link_types_display(self, obj: TeamLink) -> str:
        """Display link types as comma-separated labels.

        Args:
            obj: The TeamLink instance.

        Returns:
            Formatted string of link type labels.

        """
        return obj.link_types_display or "-"

    @admin.display(description="Visible", boolean=True)
    def is_visible_display(self, obj: TeamLink) -> bool:
        """Display whether the link is currently visible.

        Args:
            obj: The TeamLink instance.

        Returns:
            True if link is visible, False otherwise.

        """
        return obj.is_visible


@admin.register(DiscordRole)
class DiscordRoleAdmin(admin.ModelAdmin):
    """Admin for DiscordRole model."""

    list_display = (
        "name",
        "role_id",
        "color_display",
        "position",
        "managed",
        "mentionable",
        "date_synced",
    )
    list_filter = ("managed", "mentionable")
    search_fields = ("name", "role_id")
    readonly_fields = ("date_synced",)
    ordering = ("-position",)

    @admin.display(description="Color")
    def color_display(self, obj: DiscordRole) -> str:
        """Display role color as hex with colored badge.

        Args:
            obj: The DiscordRole instance.

        Returns:
            HTML span with colored background or dash if no color.

        """
        if obj.color == 0:
            return "-"
        hex_color = obj.color_hex
        from django.utils.html import format_html

        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px;">{}</span>',
            hex_color,
            hex_color,
        )


@admin.register(MembershipApplication)
class MembershipApplicationAdmin(admin.ModelAdmin):
    """Admin for MembershipApplication model."""

    list_display = (
        "discord_username",
        "server_nickname",
        "full_name_display",
        "status",
        "is_complete_display",
        "date_created",
        "date_modified",
    )
    list_filter = ("status", "agree_privacy", "agree_tos")
    search_fields = ("discord_id", "discord_username", "server_nickname", "first_name", "last_name")
    readonly_fields = (
        "id",
        "discord_id",
        "discord_username",
        "discord_user_data",
        "discord_member_data",
        "modal_form_data",
        "date_created",
        "date_modified",
    )
    raw_id_fields = ("modified_by",)
    fieldsets = (
        ("Application ID", {"fields": ("id",)}),
        (
            "Discord Identity",
            {
                "fields": (
                    "discord_id",
                    "discord_username",
                    "server_nickname",
                    "avatar_url",
                    "guild_avatar_url",
                )
            },
        ),
        (
            "Applicant Information",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "agree_privacy",
                    "agree_tos",
                    "applicant_notes",
                )
            },
        ),
        ("Admin", {"fields": ("status", "admin_notes", "modified_by")}),
        (
            "Raw Data",
            {
                "fields": (
                    "discord_user_data",
                    "discord_member_data",
                    "modal_form_data",
                ),
                "classes": ("collapse",),
            },
        ),
        ("Timestamps", {"fields": ("date_created", "date_modified")}),
    )

    @admin.display(description="Full Name")
    def full_name_display(self, obj: MembershipApplication) -> str:
        """Display full name or dash if not set.

        Args:
            obj: The MembershipApplication instance.

        Returns:
            Full name or dash.

        """
        return obj.full_name or "-"

    @admin.display(description="Complete", boolean=True)
    def is_complete_display(self, obj: MembershipApplication) -> bool:
        """Display whether application is complete.

        Args:
            obj: The MembershipApplication instance.

        Returns:
            True if complete, False otherwise.

        """
        return obj.is_complete
