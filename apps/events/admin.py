"""Admin configuration for events app."""

from typing import ClassVar

from django.contrib import admin

from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    EventRegistration,
    EventSignup,
    Race,
    Squad,
    SquadMember,
)


class SquadInline(admin.TabularInline):
    """Inline admin for squads within an event."""

    model = Squad
    extra = 0
    fields: ClassVar[list[str]] = [
        "name",
        "captain",
        "vice_captain",
        "min_zwift_racing_category",
        "max_zwift_racing_category",
    ]
    show_change_link = True


class EventSignupInline(admin.TabularInline):
    """Inline admin for signups within an event."""

    model = EventSignup
    extra = 0
    fields: ClassVar[list[str]] = ["user", "signup_timezone", "status", "created_at"]
    readonly_fields: ClassVar[list[str]] = ["created_at"]
    show_change_link = True


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Admin for Event model."""

    list_display: ClassVar[list[str]] = [
        "title",
        "start_date",
        "end_date",
        "visible",
        "signups_open",
        "created_by",
    ]
    list_filter: ClassVar[list[str]] = ["visible", "signups_open", "start_date"]
    search_fields: ClassVar[list[str]] = ["title", "description"]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    ordering: ClassVar[list[str]] = ["-start_date"]
    inlines: ClassVar[list] = [SquadInline, EventSignupInline]


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


@admin.register(EventSignup)
class EventSignupAdmin(admin.ModelAdmin):
    """Admin for EventSignup model."""

    list_display: ClassVar[list[str]] = [
        "user",
        "event",
        "signup_timezone",
        "status",
        "created_at",
    ]
    list_filter: ClassVar[list[str]] = ["status", "event"]
    search_fields: ClassVar[list[str]] = [
        "user__discord_username",
        "user__first_name",
        "user__last_name",
        "event__title",
    ]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    ordering: ClassVar[list[str]] = ["-created_at"]


class SquadMemberInline(admin.TabularInline):
    """Inline admin for members within a squad."""

    model = SquadMember
    extra = 0
    fields: ClassVar[list[str]] = ["user", "status"]


@admin.register(Squad)
class SquadAdmin(admin.ModelAdmin):
    """Admin for Squad model."""

    list_display: ClassVar[list[str]] = [
        "event",
        "name",
        "captain",
        "min_zwift_racing_category",
        "max_zwift_racing_category",
    ]
    list_filter: ClassVar[list[str]] = ["event", "name"]
    search_fields: ClassVar[list[str]] = [
        "name",
        "event__title",
    ]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at", "invite_token"]
    inlines: ClassVar[list] = [SquadMemberInline]


@admin.register(SquadMember)
class SquadMemberAdmin(admin.ModelAdmin):
    """Admin for SquadMember model."""

    list_display: ClassVar[list[str]] = [
        "squad",
        "user",
        "status",
        "created_at",
    ]
    list_filter: ClassVar[list[str]] = ["status", "squad__event", "squad"]
    search_fields: ClassVar[list[str]] = [
        "user__discord_username",
        "user__first_name",
        "user__last_name",
        "squad__name",
    ]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    ordering: ClassVar[list[str]] = ["-created_at"]


class AvailabilityResponseInline(admin.TabularInline):
    """Inline admin for responses within an availability grid."""

    model = AvailabilityResponse
    extra = 0
    fields: ClassVar[list[str]] = ["user", "available_cells", "created_at", "updated_at"]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]


@admin.register(AvailabilityGrid)
class AvailabilityGridAdmin(admin.ModelAdmin):
    """Admin for AvailabilityGrid model."""

    list_display: ClassVar[list[str]] = [
        "squad",
        "title",
        "status",
        "grid_timezone",
        "start_date",
        "end_date",
        "response_count",
        "created_by",
    ]
    list_filter: ClassVar[list[str]] = ["status", "squad__event"]
    search_fields: ClassVar[list[str]] = ["title", "squad__name", "squad__event__title"]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    inlines: ClassVar[list] = [AvailabilityResponseInline]


@admin.register(AvailabilityResponse)
class AvailabilityResponseAdmin(admin.ModelAdmin):
    """Admin for AvailabilityResponse model."""

    list_display: ClassVar[list[str]] = [
        "user",
        "grid",
        "created_at",
    ]
    list_filter: ClassVar[list[str]] = ["grid__squad__event"]
    search_fields: ClassVar[list[str]] = [
        "user__discord_username",
        "user__first_name",
        "user__last_name",
        "grid__title",
    ]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
