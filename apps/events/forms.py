"""Forms for events app."""

from typing import ClassVar

from django import forms

from apps.events.models import Event, Squad
from apps.team.models import DiscordChannel


def _get_channel_choices() -> list:
    """Build choices list for Discord channel Select widget.

    Returns grouped choices with optgroups by category name.
    Text-like channel types (text, announcement, forum) are included.

    Returns:
        List of choices with (value, label) tuples and optgroup tuples.

    """
    channels = DiscordChannel.objects.filter(
        channel_type__in=[
            DiscordChannel.ChannelType.TEXT,
            DiscordChannel.ChannelType.ANNOUNCEMENT,
            DiscordChannel.ChannelType.FORUM,
        ]
    ).order_by("category_name", "position")

    choices: list = [("0", "(none)")]

    groups: dict[str, list[tuple[str, str]]] = {}
    for ch in channels:
        group = ch.category_name or "Uncategorized"
        groups.setdefault(group, []).append((ch.channel_id, f"#{ch.name}"))

    for group_label, group_choices in groups.items():
        choices.append((group_label, group_choices))

    return choices


class EventForm(forms.ModelForm):
    """Form for creating and editing events."""

    discord_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    class Meta:
        """Meta options for EventForm."""

        model = Event
        fields: ClassVar[list[str]] = [
            "title",
            "logo",
            "description",
            "start_date",
            "end_date",
            "url",
            "discord_channel_id",
            "head_captain_role_id",
            "visible",
            "signups_open",
            "signup_instructions",
            "timezone_options",
            "timezone_required",
        ]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Event title"},
            ),
            "logo": forms.ClearableFileInput(
                attrs={"class": "file-input file-input-bordered w-full", "accept": "image/*"},
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 4,
                    "placeholder": "Event description (supports Markdown)",
                },
            ),
            "start_date": forms.DateInput(
                attrs={"class": "input input-bordered w-full", "type": "date"},
                format="%Y-%m-%d",
            ),
            "end_date": forms.DateInput(
                attrs={"class": "input input-bordered w-full", "type": "date"},
                format="%Y-%m-%d",
            ),
            "url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
            "head_captain_role_id": forms.NumberInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Discord role ID (0 = none)"},
            ),
            "visible": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "signups_open": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
            "signup_instructions": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 3,
                    "placeholder": "Instructions shown to users on the signup form",
                },
            ),
            "timezone_options": forms.HiddenInput(),
            "timezone_required": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with Discord channel choices."""
        super().__init__(*args, **kwargs)
        choices = _get_channel_choices()

        # Convert current model value (int) to string for the Select widget
        current_value = str(self.initial.get("discord_channel_id", 0) or 0)

        # If current value isn't in choices, add a fallback
        all_values = self._flat_choice_values(choices)
        if current_value != "0" and current_value not in all_values:
            choices.append((current_value, f"Unknown Channel ({current_value})"))

        self.fields["discord_channel_id"].widget.choices = choices
        self.initial["discord_channel_id"] = current_value

    @staticmethod
    def _flat_choice_values(choices: list) -> set[str]:
        """Extract all values from a choices list including optgroups.

        Args:
            choices: Django choices list with possible optgroup tuples.

        Returns:
            Set of all choice values.

        """
        values = set()
        for item in choices:
            if isinstance(item[1], list | tuple) and item[1] and isinstance(item[1][0], tuple | list):
                for val, _label in item[1]:
                    values.add(str(val))
            else:
                values.add(str(item[0]))
        return values

    def clean_discord_channel_id(self) -> int:
        """Convert selected channel ID string back to int for the model.

        Returns:
            Channel ID as integer (0 for none).

        """
        value = self.cleaned_data.get("discord_channel_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0


class SquadForm(forms.ModelForm):
    """Form for creating and editing squads."""

    discord_channel_id = forms.CharField(
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

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
            "url",
            "invite_url",
        ]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Squad name"},
            ),
            "squad_timezone": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "e.g., America/New_York"},
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
            "url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
            "invite_url": forms.URLInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "https://..."},
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with Discord channel choices."""
        super().__init__(*args, **kwargs)
        choices = _get_channel_choices()

        current_value = str(self.initial.get("discord_channel_id", 0) or 0)

        all_values = EventForm._flat_choice_values(choices)
        if current_value != "0" and current_value not in all_values:
            choices.append((current_value, f"Unknown Channel ({current_value})"))

        self.fields["discord_channel_id"].widget.choices = choices
        self.initial["discord_channel_id"] = current_value

    def clean_discord_channel_id(self) -> int:
        """Convert selected channel ID string back to int for the model.

        Returns:
            Channel ID as integer (0 for none).

        """
        value = self.cleaned_data.get("discord_channel_id", "0")
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
