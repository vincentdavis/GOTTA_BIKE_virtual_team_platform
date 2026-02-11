"""Forms for events app."""

from typing import ClassVar

from django import forms

from apps.events.models import Event


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
            "visible",
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
            "visible": forms.CheckboxInput(
                attrs={"class": "checkbox"},
            ),
        }
