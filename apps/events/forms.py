"""Forms for events app."""

from typing import ClassVar

from django import forms

from apps.events.models import Event, Squad


class EventForm(forms.ModelForm):
    """Form for creating and editing events."""

    class Meta:
        """Meta options for EventForm."""

        model = Event
        fields: ClassVar[list[str]] = [
            "title",
            "description",
            "start_date",
            "end_date",
            "url",
            "discord_channel_id",
            "visible",
            "signups_open",
            "timezone_options",
            "timezone_required",
        ]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Event title"},
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 4,
                    "placeholder": "Event description (supports Markdown)",
                },
            ),
            "start_date": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "end_date": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
            "discord_channel_id": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Discord channel ID (0 = none)"},
            ),
            "visible": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "signups_open": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "timezone_options": forms.HiddenInput(),
            "timezone_required": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
        }


class SquadForm(forms.ModelForm):
    """Form for creating and editing squads."""

    class Meta:
        """Meta options for SquadForm."""

        model = Squad
        fields: ClassVar[list[str]] = [
            "name",
            "squad_timezone",
            "discord_channel_id",
            "captain",
            "vice_captain",
            "team_discord_role",
            "min_zwift_category",
            "max_zwift_category",
            "min_zwift_racing_category",
            "max_zwift_racing_category",
        ]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Squad name"},
            ),
            "squad_timezone": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., America/New_York"},
            ),
            "discord_channel_id": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Discord channel ID (0 = none)"},
            ),
            "captain": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "vice_captain": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "team_discord_role": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Discord role ID (0 = none)"},
            ),
            "min_zwift_category": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., A, B, C, D, E"},
            ),
            "max_zwift_category": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., A, B, C, D, E"},
            ),
            "min_zwift_racing_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
            "max_zwift_racing_category": forms.Select(
                attrs={"class": "select select-bordered w-full"},
            ),
        }
