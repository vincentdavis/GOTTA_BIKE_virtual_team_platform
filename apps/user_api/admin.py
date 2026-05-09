"""Admin registration for user API keys."""

from typing import ClassVar

from django.contrib import admin

from apps.user_api.models import UserApiKey


@admin.register(UserApiKey)
class UserApiKeyAdmin(admin.ModelAdmin):
    """Admin for UserApiKey. Read-mostly — keys can only be revoked here."""

    list_display: ClassVar[list[str]] = [
        "user",
        "name",
        "prefix",
        "last4",
        "created_at",
        "expires_at",
        "revoked_at",
        "last_used_at",
    ]
    list_filter: ClassVar[list[str]] = ["revoked_at", "expires_at"]
    search_fields: ClassVar[list[str]] = [
        "user__username",
        "user__discord_username",
        "name",
        "prefix",
    ]
    readonly_fields: ClassVar[list[str]] = [
        "user",
        "name",
        "key_hash",
        "prefix",
        "last4",
        "created_at",
        "expires_at",
        "last_used_at",
    ]
