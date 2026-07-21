"""Forms for race-verified users to curate PowerUps."""

from typing import ClassVar

from django import forms

from apps.ttt_planner.models import PowerUp

_INPUT = {"class": "input input-bordered w-full"}
_CHECK = {"class": "checkbox"}
_AREA = {"class": "textarea textarea-bordered w-full", "rows": 3}
_FILE = {"class": "file-input file-input-bordered w-full"}


class PowerUpForm(forms.ModelForm):
    """Create/edit a Zwift PowerUp shown on the routes reference page."""

    class Meta:
        """Form metadata."""

        model = PowerUp
        fields: ClassVar[list[str]] = [
            "name",
            "aka",
            "effect",
            "duration_seconds",
            "icon",
            "discord_emoji",
            "event_only",
            "excluded_from_ladder",
            "order",
            "is_active",
        ]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(attrs=_INPUT),
            "aka": forms.TextInput(attrs=_INPUT),
            "effect": forms.Textarea(attrs=_AREA),
            "duration_seconds": forms.NumberInput(attrs=_INPUT),
            "icon": forms.ClearableFileInput(attrs=_FILE),
            "discord_emoji": forms.TextInput(attrs=_INPUT),
            "event_only": forms.CheckboxInput(attrs=_CHECK),
            "excluded_from_ladder": forms.CheckboxInput(attrs=_CHECK),
            "order": forms.NumberInput(attrs=_INPUT),
            "is_active": forms.CheckboxInput(attrs=_CHECK),
        }
