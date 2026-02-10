"""Admin configuration for events app."""

from typing import ClassVar

from django.contrib import admin

from apps.events.models import Event, EventRegistration, Race


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Admin for Event model."""

    list_display: ClassVar[list[str]] = [
        "title",
        "start_date",
        "end_date",
        "visible",
        "created_by",
    ]
    list_filter: ClassVar[list[str]] = ["visible", "start_date"]
    search_fields: ClassVar[list[str]] = ["title", "description"]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    ordering: ClassVar[list[str]] = ["-start_date"]


@admin.register(Race)
class RaceAdmin(admin.ModelAdmin):
    """Admin for Race model."""

    list_display: ClassVar[list[str]] = [
        "title",
        "event",
        "start_date",
        "start_time",
        "zwift_category",
        "created_by",
    ]
    list_filter: ClassVar[list[str]] = ["event", "zwift_category", "start_date"]
    search_fields: ClassVar[list[str]] = ["title", "description", "event__title"]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    ordering: ClassVar[list[str]] = ["-start_date"]


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    """Admin for EventRegistration model."""

    list_display: ClassVar[list[str]] = [
        "user",
        "race",
        "status",
        "created_at",
    ]
    list_filter: ClassVar[list[str]] = ["status", "race__event", "race"]
    search_fields: ClassVar[list[str]] = [
        "user__discord_username",
        "user__first_name",
        "user__last_name",
        "race__title",
    ]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    ordering: ClassVar[list[str]] = ["-created_at"]
