"""Admin registration for the tickets app."""

from django.contrib import admin

from apps.tickets.models import Ticket


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    """Django admin configuration for Ticket."""

    list_display = (
        "pk",
        "title",
        "status",
        "category",
        "priority",
        "submitted_by",
        "assigned_to",
        "created_at",
    )
    list_filter = ("status", "category", "priority")
    search_fields = (
        "title",
        "details",
        "submitted_by__username",
        "submitted_by__discord_username",
        "submitted_by__first_name",
        "submitted_by__last_name",
    )
    raw_id_fields = ("submitted_by", "assigned_to", "closed_by")
    readonly_fields = ("created_at", "updated_at", "closed_at")
    list_select_related = ("submitted_by", "assigned_to")
    fieldsets = (
        (None, {"fields": ("title", "details", "status", "category", "priority", "resolution")}),
        ("People", {"fields": ("submitted_by", "assigned_to", "closed_by")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "closed_at")}),
    )
