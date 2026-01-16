"""Forms for team app."""

from typing import ClassVar

from django import forms

from apps.team.converters import inches_to_cm, lbs_to_kg
from apps.team.models import MembershipApplication, RaceReadyRecord, TeamLink


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

    def __init__(
        self,
        *args,
        allowed_types: list[str] | None = None,
        unit_preference: str = "metric",
        **kwargs,
    ) -> None:
        """Initialize form with optional allowed_types filter and unit preference.

        Args:
            *args: Positional arguments passed to parent.
            allowed_types: List of verify_type values to allow. If provided, filters choices.
            unit_preference: User's unit preference ('metric' or 'imperial').
            **kwargs: Keyword arguments passed to parent.

        """
        super().__init__(*args, **kwargs)
        self.unit_preference = unit_preference

        if allowed_types:
            # Filter choices to only allowed types
            self.fields["verify_type"].choices = [
                (value, label) for value, label in self.ALL_VERIFY_TYPE_CHOICES if value in allowed_types
            ]

        # Update field attributes based on unit preference
        if unit_preference == "imperial":
            # Update weight field for lbs
            self.fields["weight"].widget.attrs.update({
                "placeholder": "e.g., 160",
                "min": "44",   # ~20 kg
                "max": "441",  # ~200 kg
                "step": "0.1",
            })
            self.fields["weight"].label = "Weight (lbs)"

            # Update height field for inches
            self.fields["height"].widget.attrs.update({
                "placeholder": "e.g., 69",
                "min": "39",   # ~100 cm
                "max": "98",   # ~250 cm
            })
            self.fields["height"].label = "Height (inches)"
        else:
            # Ensure metric labels are set
            self.fields["weight"].label = "Weight (kg)"
            self.fields["height"].label = "Height (cm)"

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

    def clean_weight(self):
        """Validate and convert weight to kg if needed.

        Returns:
            Weight in kg (Decimal).

        """
        weight = self.cleaned_data.get("weight")
        if weight and self.unit_preference == "imperial":
            # Convert from lbs to kg
            weight = lbs_to_kg(weight)
        return weight

    def clean_height(self):
        """Validate and convert height to cm if needed.

        Returns:
            Height in cm (int).

        """
        height = self.cleaned_data.get("height")
        if height and self.unit_preference == "imperial":
            # Convert from inches to cm
            height = inches_to_cm(height)
        return height

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


class MembershipApplicationApplicantForm(forms.ModelForm):
    """Form for applicants to complete their membership application.

    This form is used on the public application page accessed via UUID link.
    Applicants can fill in their name, agree to policies, and add notes.
    """

    class Meta:
        """Meta options for MembershipApplicationApplicantForm."""

        model = MembershipApplication
        fields: ClassVar[list[str]] = [
            "first_name",
            "last_name",
            "agree_privacy",
            "agree_tos",
            "applicant_notes",
        ]
        widgets: ClassVar[dict] = {
            "first_name": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "First name",
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Last name",
                }
            ),
            "agree_privacy": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary"}
            ),
            "agree_tos": forms.CheckboxInput(
                attrs={"class": "checkbox checkbox-primary"}
            ),
            "applicant_notes": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 4,
                    "placeholder": "Optional notes for the team",
                }
            ),
        }

    def clean(self):
        """Validate that required agreements are checked.

        Returns:
            The cleaned data.

        Raises:
            ValidationError: If agreements are not checked.

        """
        cleaned_data = super().clean()

        if not cleaned_data.get("agree_privacy"):
            self.add_error("agree_privacy", "You must agree to the privacy policy.")
        if not cleaned_data.get("agree_tos"):
            self.add_error("agree_tos", "You must agree to the terms of service.")

        return cleaned_data


class MembershipApplicationAdminForm(forms.ModelForm):
    """Form for admins to update application status and notes.

    This form is used on the admin detail page to update status
    and add internal notes about the application.
    """

    class Meta:
        """Meta options for MembershipApplicationAdminForm."""

        model = MembershipApplication
        fields: ClassVar[list[str]] = ["status", "admin_notes"]
        widgets: ClassVar[dict] = {
            "status": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
            "admin_notes": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 4,
                    "placeholder": "Internal notes (not visible to applicant)",
                }
            ),
        }
