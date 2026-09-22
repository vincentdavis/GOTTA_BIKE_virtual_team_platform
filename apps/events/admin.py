"""Admin configuration for events app."""

from typing import ClassVar

from django import forms
from django.contrib import admin

from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    EventSignup,
    Race,
    RaceRegistration,
    Squad,
    SquadMember,
)
from apps.events.squad_tags import clean_event_tags, clean_tag_list, prune_squad_tags


class SquadInline(admin.TabularInline):
    """Inline admin for squads within an event."""

    model = Squad
    extra = 0
    fields: ClassVar[list[str]] = [
        "name",
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


class EventAdminForm(forms.ModelForm):
    """Event change form, holding squad tags to the same rules as the event edit page.

    No Meta: ModelAdmin.get_form builds it with the model and the admin's own field list.
    """

    def clean_squad_tags(self) -> list[str]:
        """Type-check, normalise and bound the squad tags typed into the JSON box.

        Returns:
            The normalised tags.

        """
        return clean_event_tags(self.cleaned_data.get("squad_tags"))


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Admin for Event model."""

    form = EventAdminForm

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

    def save_related(self, request, form, formsets, change) -> None:
        """Prune every squad's tags to the event's list once the squad inline is saved too.

        Done here rather than in save_model: the inline saves its changed squads after
        save_model, from rows read before it, and would write the old tags back.

        Args:
            request: The admin request.
            form: The saved event form.
            formsets: The inline formsets.
            change: Whether this was an edit rather than an add.

        """
        super().save_related(request, form, formsets, change)
        prune_squad_tags(form.instance)


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


@admin.register(RaceRegistration)
class RaceRegistrationAdmin(admin.ModelAdmin):
    """Admin for RaceRegistration model."""

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


class SquadAdminForm(forms.ModelForm):
    """Squad change form: an emptied Tags box means no tags, not a NULL the column refuses.

    No Meta: ModelAdmin.get_form builds it with the model and the admin's own field list.
    """

    def clean_tags(self) -> list[str]:
        """Type-check and normalise the tags typed into the JSON box.

        ``save_model`` then holds them to the event's list.

        Returns:
            The normalised tags (``[]`` for an empty box or ``null``).

        """
        return clean_tag_list(self.cleaned_data.get("tags"))


@admin.register(Squad)
class SquadAdmin(admin.ModelAdmin):
    """Admin for Squad model."""

    form = SquadAdminForm

    list_display: ClassVar[list[str]] = [
        "event",
        "name",
        "captains_display",
        "min_zwift_racing_category",
        "max_zwift_racing_category",
    ]
    list_filter: ClassVar[list[str]] = ["event", "name"]
    search_fields: ClassVar[list[str]] = [
        "name",
        "event__title",
    ]
    readonly_fields: ClassVar[list[str]] = ["created_at", "updated_at", "invite_token"]
    filter_horizontal: ClassVar[list[str]] = ["captains", "vice_captains"]
    inlines: ClassVar[list] = [SquadMemberInline]

    def save_model(self, request, obj: Squad, form, change) -> None:
        """Save the squad, then hold its tags to its event's list.

        The JSON box takes anything, but the event's squad tags are the only source: a tag
        the event does not list is dropped and a case variant takes the event's spelling.

        Args:
            request: The admin request.
            obj: The squad being saved.
            form: The admin form.
            change: Whether this was an edit rather than an add.

        """
        super().save_model(request, obj, form, change)
        prune_squad_tags(obj.event)

    @admin.display(description="Captains")
    def captains_display(self, obj: Squad) -> str:
        """Return a comma-separated list of squad captain names for the list view.

        Args:
            obj: The squad being rendered.

        Returns:
            Comma-separated captain names, or "-" when there are none.

        """
        names = [u.get_full_name() or u.discord_username or u.username for u in obj.captains.all()]
        return ", ".join(names) or "-"


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
