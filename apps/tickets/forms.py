"""Forms for the tickets app."""

from typing import ClassVar

from django import forms

from apps.tickets.models import Ticket


class TicketCreateForm(forms.ModelForm):
    """Form members use to submit a new ticket."""

    class Meta:
        """Meta options for TicketCreateForm."""

        model = Ticket
        fields: ClassVar[list[str]] = ["title", "details", "category", "priority"]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Short summary"},
            ),
            "details": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 6,
                    "placeholder": "Describe the issue (supports Markdown)",
                },
            ),
            "category": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "priority": forms.Select(attrs={"class": "select select-bordered w-full"}),
        }


class TicketEditForm(forms.ModelForm):
    """Form for editing tickets — adds status / assignee / resolution."""

    class Meta:
        """Meta options for TicketEditForm."""

        model = Ticket
        fields: ClassVar[list[str]] = [
            "title",
            "details",
            "category",
            "priority",
            "status",
            "assigned_to",
            "resolution",
        ]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "details": forms.Textarea(
                attrs={"class": "textarea textarea-bordered w-full", "rows": 6},
            ),
            "category": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "priority": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "status": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "assigned_to": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "resolution": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 3,
                    "placeholder": "Short closing note (recommended when closing)",
                },
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize the form and label the assignee dropdown with human names.

        Args:
            *args: Positional arguments passed to ModelForm.
            **kwargs: Keyword arguments passed to ModelForm.

        """
        super().__init__(*args, **kwargs)
        # Late import avoids a circular dependency at module load time.
        from apps.accounts.models import User

        qs = User.objects.order_by("first_name", "last_name", "discord_username")
        self.fields["assigned_to"].queryset = qs
        self.fields["assigned_to"].label_from_instance = lambda u: (
            f"{u.first_name} {u.last_name}".strip()
            or u.discord_nickname
            or u.discord_username
            or u.username
        )
