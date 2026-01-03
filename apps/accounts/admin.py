"""Admin configuration for accounts app."""

import json
from typing import Any, ClassVar

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path

from apps.accounts.models import GuildMember, Permissions, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin for custom User model."""

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "discord_username",
        "zwid",
        "is_staff",
    )
    list_filter = (*BaseUserAdmin.list_filter, "discord_id")
    search_fields = (
        *BaseUserAdmin.search_fields,
        "discord_id",
        "discord_username",
        "discord_nickname",
        "zwid",
    )

    fieldsets = (
        *BaseUserAdmin.fieldsets,
        (
            "Discord",
            {
                "fields": (
                    "discord_id",
                    "discord_username",
                    "discord_nickname",
                    "discord_avatar",
                    "discord_roles",
                ),
            },
        ),
        (
            "Zwift",
            {
                "fields": (
                    "zwid",
                    "zwid_verified",
                ),
            },
        ),
        (
            "Personal Info",
            {
                "fields": ("birth_year",),
            },
        ),
        (
            "Permissions",
            {
                "fields": ("roles", "permission_overrides"),
                "description": "Legacy roles and manual permission overrides. "
                "Permissions are primarily granted via Discord roles configured in Constance.",
            },
        ),
    )

    add_fieldsets = (
        *BaseUserAdmin.add_fieldsets,
        (
            "Integrations",
            {
                "fields": (
                    "discord_id",
                    "discord_username",
                    "discord_nickname",
                    "zwid",
                ),
            },
        ),
    )

    def get_urls(self) -> list:
        """Add custom URLs for permission mappings view.

        Returns:
            List of URL patterns including custom permission mappings URL.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "permission-mappings/",
                self.admin_site.admin_view(self.permission_mappings_view),
                name="accounts_permission_mappings",
            ),
        ]
        return custom_urls + urls

    def permission_mappings_view(self, request: HttpRequest) -> HttpResponse:
        """Edit permission-to-Discord-role mappings.

        Args:
            request: The HTTP request.

        Returns:
            Rendered permission mappings template.

        """
        from constance import config

        from apps.team.models import DiscordRole

        # Check permissions
        if not request.user.is_superuser and not request.user.is_app_admin:
            messages.error(request, "You don't have permission to access this page.")
            return redirect("admin:index")

        # Get available Discord roles (exclude managed/bot roles)
        available_roles = DiscordRole.objects.filter(managed=False).order_by("-position")

        if request.method == "POST":
            # Save each permission's selected roles to Constance
            for perm_name, constance_key in Permissions.CONSTANCE_MAP.items():
                selected_role_ids = request.POST.getlist(f"perm_{perm_name}")
                setattr(config, constance_key, json.dumps(selected_role_ids))
            messages.success(request, "Permission mappings saved successfully.")
            return redirect(request.path)

        # Load current mappings from Constance
        current_mappings: dict[str, list[str]] = {}
        for perm_name, constance_key in Permissions.CONSTANCE_MAP.items():
            role_ids_json = getattr(config, constance_key, "[]")
            try:
                current_mappings[perm_name] = json.loads(role_ids_json)
            except json.JSONDecodeError:
                current_mappings[perm_name] = []

        context = {
            **self.admin_site.each_context(request),
            "title": "Permission Mappings",
            "permissions": Permissions.CHOICES,
            "available_roles": available_roles,
            "current_mappings": current_mappings,
            "opts": self.model._meta,
        }
        return render(request, "admin/accounts/permission_mappings.html", context)


@admin.register(GuildMember)
class GuildMemberAdmin(admin.ModelAdmin):
    """Admin configuration for GuildMember model."""

    change_list_template = "admin/accounts/guildmember/change_list.html"

    list_display: ClassVar[list[str]] = [
        "display_name_or_username",
        "discord_id",
        "user_link",
        "is_bot",
        "joined_at",
        "status_display",
        "date_modified",
    ]
    list_filter: ClassVar[list[str]] = ["is_bot", "date_left"]
    search_fields: ClassVar[list[str]] = ["discord_id", "username", "display_name", "nickname"]
    ordering: ClassVar[list[str]] = ["-date_modified"]
    readonly_fields: ClassVar[list[str]] = ["date_created", "date_modified", "date_left"]
    raw_id_fields: ClassVar[list[str]] = ["user"]

    fieldsets: ClassVar[list[tuple[str | None, dict[str, Any]]]] = [
        (None, {"fields": ["discord_id", "username", "display_name", "nickname"]}),
        ("Avatar", {"fields": ["avatar_hash"]}),
        ("Discord Data", {"fields": ["roles", "joined_at", "is_bot"]}),
        ("Linked Account", {"fields": ["user"]}),
        ("Tracking", {"fields": ["date_created", "date_modified", "date_left"]}),
    ]

    def display_name_or_username(self, obj: GuildMember) -> str:
        """Return display name or username.

        Args:
            obj: The GuildMember instance.

        Returns:
            The member's nickname, display name, or username.

        """
        return obj.nickname or obj.display_name or obj.username

    display_name_or_username.short_description = "Name"  # type: ignore[attr-defined]

    def user_link(self, obj: GuildMember) -> str:
        """Return link to user or dash if none.

        Args:
            obj: The GuildMember instance.

        Returns:
            The linked user's username or a dash if not linked.

        """
        if obj.user:
            return obj.user.username
        return "-"

    user_link.short_description = "User Account"  # type: ignore[attr-defined]

    def status_display(self, obj: GuildMember) -> str:
        """Return membership status.

        Args:
            obj: The GuildMember instance.

        Returns:
            'Left' if member has left, 'Active' otherwise.

        """
        if obj.date_left:
            return "Left"
        return "Active"

    status_display.short_description = "Status"  # type: ignore[attr-defined]

    def get_urls(self) -> list:
        """Add custom URLs for comparison view.

        Returns:
            List of URL patterns including custom comparison URL.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "comparison/",
                self.admin_site.admin_view(self.comparison_view),
                name="accounts_guildmember_comparison",
            ),
        ]
        return custom_urls + urls

    def comparison_view(self, request: HttpRequest) -> HttpResponse:
        """Show comparison of guild members vs user accounts.

        Args:
            request: The HTTP request.

        Returns:
            Rendered comparison template.

        """
        # Get filter from query params
        filter_status = request.GET.get("status", "all")

        # Active guild members (not bots, not left)
        active_members = GuildMember.objects.filter(
            is_bot=False,
            date_left__isnull=True,
        ).select_related("user")

        # Guild members without user accounts
        guild_only = active_members.filter(user__isnull=True)

        # Guild members with user accounts
        guild_and_user = active_members.filter(user__isnull=False)

        # Users who left the guild (have user but date_left is set)
        left_guild = GuildMember.objects.filter(
            is_bot=False,
            date_left__isnull=False,
            user__isnull=False,
        ).select_related("user")

        # Discord OAuth users with no GuildMember record
        # NOTE: Only considers users who logged in via Discord OAuth (have discord_id).
        # Regular Django accounts (staff, admin) without discord_id are NOT included.
        discord_users_without_member = User.objects.filter(
            discord_id__isnull=False,
        ).exclude(
            discord_id="",
        ).exclude(
            discord_id__in=GuildMember.objects.values_list("discord_id", flat=True),
        )

        # Apply filter
        if filter_status == "guild_only":
            display_members: QuerySet = guild_only
        elif filter_status == "linked":
            display_members = guild_and_user
        elif filter_status == "left":
            display_members = left_guild
        else:
            display_members = active_members

        context = {
            **self.admin_site.each_context(request),
            "title": "Guild Membership Comparison",
            "filter_status": filter_status,
            "display_members": display_members,
            "guild_only_count": guild_only.count(),
            "linked_count": guild_and_user.count(),
            "left_count": left_guild.count(),
            "discord_users_no_guild_count": discord_users_without_member.count(),
            "discord_users_no_guild": discord_users_without_member if filter_status == "discord_no_guild" else [],
            "total_active": active_members.count(),
        }

        return render(request, "admin/accounts/guildmember/comparison.html", context)
