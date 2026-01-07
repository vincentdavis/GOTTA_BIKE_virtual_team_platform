"""Forms for team app."""

from typing import ClassVar

from django import forms

from apps.team.models import RaceReadyRecord, TeamLink


class TeamLinkForm(forms.ModelForm):
    """Form for submitting team links."""

    link_types = forms.MultipleChoiceField(
        choices=TeamLink.LinkType.choices,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-sm"}),
        required=False,
        help_text="Select one or more types for this link",
    )

    class Meta:
        """Meta options for TeamLinkForm."""

        model = TeamLink
        fields: ClassVar[list[str]] = ["title", "description", "url", "link_types", "date_open", "date_closed"]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Link title"}),
            "description": forms.Textarea(
                attrs={"class": "textarea textarea-bordered w-full", "rows": 3, "placeholder": "Optional description"}
            ),
            "url": forms.URLInput(attrs={"class": "input input-bordered w-full", "placeholder": "https://..."}),
            "date_open": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "date_closed": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }


class TeamLinkEditForm(forms.ModelForm):
    """Form for editing team links (includes active field)."""

    link_types = forms.MultipleChoiceField(
        choices=TeamLink.LinkType.choices,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-sm"}),
        required=False,
        help_text="Select one or more types for this link",
    )

    class Meta:
        """Meta options for TeamLinkEditForm."""

        model = TeamLink
        fields: ClassVar[list[str]] = [
            "title", "description", "url", "link_types", "date_open", "date_closed", "active",
        ]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Link title"}),
            "description": forms.Textarea(
                attrs={"class": "textarea textarea-bordered w-full", "rows": 3, "placeholder": "Optional description"}
            ),
            "url": forms.URLInput(attrs={"class": "input input-bordered w-full", "placeholder": "https://..."}),
            "date_open": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "date_closed": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class RaceReadyRecordForm(forms.ModelForm):
    """Form for submitting race ready verification records."""

    # All possible verify_type choices
    ALL_VERIFY_TYPE_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("weight_full", "Weight Full"),
        ("weight_light", "Weight Light"),
        ("height", "Height"),
        ("power", "Power"),
    ]

    class Meta:
        """Meta options for RaceReadyRecordForm."""

        model = RaceReadyRecord
        fields: ClassVar[list[str]] = [
            "verify_type", "media_type", "weight", "height", "ftp", "media_file", "url", "notes", "same_gender",
        ]
        widgets: ClassVar[dict] = {
            "verify_type": forms.Select(attrs={"class": "select select-bordered w-full", "id": "id_verify_type"}),
            "media_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "weight": forms.NumberInput(attrs={
                "class": "input input-bordered w-full",
                "placeholder": "e.g., 72.5",
                "step": "0.01",
                "min": "20",
                "max": "200",
            }),
            "height": forms.NumberInput(attrs={
                "class": "input input-bordered w-full",
                "placeholder": "e.g., 175",
                "min": "100",
                "max": "250",
            }),
            "ftp": forms.NumberInput(attrs={
                "class": "input input-bordered w-full",
                "placeholder": "e.g., 280",
                "min": "50",
                "max": "600",
            }),
            "media_file": forms.FileInput(attrs={"class": "file-input file-input-bordered w-full"}),
            "url": forms.URLInput(attrs={"class": "input input-bordered w-full", "placeholder": "https://..."}),
            "notes": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
            "same_gender": forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary"}),
        }

    def __init__(self, *args, allowed_types: list[str] | None = None, **kwargs) -> None:
        """Initialize form with optional allowed_types filter.

        Args:
            *args: Positional arguments passed to parent.
            allowed_types: List of verify_type values to allow. If provided, filters choices.
            **kwargs: Keyword arguments passed to parent.

        """
        super().__init__(*args, **kwargs)

        if allowed_types:
            # Filter choices to only allowed types
            self.fields["verify_type"].choices = [
                (value, label) for value, label in self.ALL_VERIFY_TYPE_CHOICES if value in allowed_types
            ]

    def clean_media_file(self):
        """Validate media file size and type.

        Returns:
            The cleaned media file.

        Raises:
            ValidationError: If file is too large or wrong type.

        """
        media_file = self.cleaned_data.get("media_file")
        if media_file:
            # Limit file size to 50MB
            if media_file.size > 50 * 1024 * 1024:
                raise forms.ValidationError("File size must be under 50MB.")

            # Check file extension
            allowed_extensions = [".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".avi", ".webm"]
            ext = media_file.name.lower().split(".")[-1]
            if f".{ext}" not in allowed_extensions:
                raise forms.ValidationError(
                    f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"
                )
        return media_file

    def clean(self):
        """Validate form data.

        Returns:
            The cleaned data.

        Raises:
            ValidationError: If validation fails.

        """
        cleaned_data = super().clean()
        media_file = cleaned_data.get("media_file")
        url = cleaned_data.get("url")
        verify_type = cleaned_data.get("verify_type")
        weight = cleaned_data.get("weight")
        height = cleaned_data.get("height")
        ftp = cleaned_data.get("ftp")

        # Require file or URL
        if not media_file and not url:
            raise forms.ValidationError("You must provide either a file upload or a URL (or both).")

        # Require appropriate measurement field based on verify_type
        if verify_type in ("weight_full", "weight_light") and not weight:
            self.add_error("weight", "Weight is required for weight verification.")
        if verify_type == "height" and not height:
            self.add_error("height", "Height is required for height verification.")
        if verify_type == "power" and not ftp:
            self.add_error("ftp", "FTP is required for power verification.")

        return cleaned_data
