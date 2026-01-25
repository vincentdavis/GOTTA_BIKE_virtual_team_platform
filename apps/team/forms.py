"""Forms for team app."""

import json
from datetime import date
from typing import ClassVar
from zoneinfo import available_timezones

import logfire
from constance import config
from django import forms
from django_countries.widgets import CountrySelectWidget

from apps.team.converters import inches_to_cm, lbs_to_kg
from apps.team.models import MembershipApplication, RaceReadyRecord, TeamLink

# Common timezones sorted by region
TIMEZONE_CHOICES = [
    ("", "Select timezone..."),
    *sorted(
        [(tz, tz.replace("_", " ")) for tz in available_timezones() if "/" in tz],
        key=lambda x: x[0],
    ),
]

# Modal form choices (from join_coalition cog)
REASON_CHOICES = [
    ("Virtual Racing", "Virtual Racing"),
    ("Fitness and Training", "Fitness and Training"),
    ("Community", "Community"),
]

PLATFORM_CHOICES = [
    ("Zwift", "Zwift"),
    ("Rouvy", "Rouvy"),
    ("MyWhoosh", "MyWhoosh"),
    ("TrainingPeaks Virtual", "TrainingPeaks Virtual"),
    ("Other", "Other"),
]

RACE_SERIES_CHOICES = [
    ("ZRL", "ZRL"),
    ("TTT", "TTT"),
    ("ClubLadder", "ClubLadder"),
    ("FRR", "FRR"),
    ("Women's Racing", "Women's Racing"),
    ("Other", "Other"),
]


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
                logfire.warning(
                    "RaceReadyRecordForm media file validation failed",
                    file_name=media_file.name,
                    file_size=media_file.size,
                    error_reason="file_too_large",
                )
                raise forms.ValidationError("File size must be under 50MB.")

            # Check file extension
            allowed_extensions = [".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".avi", ".webm"]
            ext = media_file.name.lower().split(".")[-1]
            if f".{ext}" not in allowed_extensions:
                logfire.warning(
                    "RaceReadyRecordForm media file validation failed",
                    file_name=media_file.name,
                    file_size=media_file.size,
                    error_reason="invalid_file_type",
                )
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
        missing_fields = []
        if verify_type in ("weight_full", "weight_light") and not weight:
            self.add_error("weight", "Weight is required for weight verification.")
            missing_fields.append("weight")
        if verify_type == "height" and not height:
            self.add_error("height", "Height is required for height verification.")
            missing_fields.append("height")
        if verify_type == "power" and not ftp:
            self.add_error("ftp", "FTP is required for power verification.")
            missing_fields.append("ftp")

        if missing_fields:
            logfire.warning(
                "RaceReadyRecordForm required fields missing",
                verify_type=verify_type,
                missing_fields=missing_fields,
            )

        return cleaned_data


class MembershipApplicationApplicantForm(forms.ModelForm):
    """Form for applicants to complete their membership application.

    This form is used on the public application page accessed via UUID link.
    Applicants can fill in their profile info, agree to policies, and add notes.
    """

    # Modal form fields (stored in modal_form_data JSONField)
    how_heard = forms.CharField(
        label="How did you hear about THE COALITION?",
        max_length=500,
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "e.g., Zwift race, friend, social media, etc.",
            }
        ),
    )

    reasons = forms.MultipleChoiceField(
        label="Why would you like to join The Coalition?",
        choices=REASON_CHOICES,
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-primary"}),
        help_text="Select 1-3 options",
    )

    know_someone = forms.CharField(
        label="Do you know someone on the team?",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Who? (leave blank if no)",
            }
        ),
    )

    platforms = forms.MultipleChoiceField(
        label="Which virtual cycling platforms do you use?",
        choices=PLATFORM_CHOICES,
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-primary"}),
        help_text="Select 1-5 options",
    )

    race_series = forms.MultipleChoiceField(
        label="Zwift race series interest",
        choices=RACE_SERIES_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox checkbox-primary"}),
        help_text="Optional - select any that interest you",
    )

    other_race_series = forms.CharField(
        label="Other race series",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Other race series you're interested in",
            }
        ),
    )

    class Meta:
        """Meta options for MembershipApplicationApplicantForm."""

        model = MembershipApplication
        fields: ClassVar[list[str]] = [
            "first_name",
            "last_name",
            "email",
            "country",
            "timezone",
            "birth_year",
            "gender",
            "unit_preference",
            "trainer",
            "power_meter",
            "dual_recording",
            "heartrate_monitor",
            "strava_profile",
            "tpv_profile_url",
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
            "email": forms.EmailInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "your.email@example.com",
                }
            ),
            "country": CountrySelectWidget(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "timezone": forms.Select(
                choices=TIMEZONE_CHOICES,
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "birth_year": forms.NumberInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "e.g., 1990",
                    "min": 1900,
                    "max": 2020,
                }
            ),
            "gender": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "unit_preference": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "trainer": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "power_meter": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "dual_recording": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "heartrate_monitor": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "strava_profile": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://www.strava.com/athletes/1234",
                }
            ),
            "tpv_profile_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://tpvirtualhub.com/profile/...",
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
        labels: ClassVar[dict[str, str]] = {
            "first_name": "First Name",
            "last_name": "Last Name",
            "email": "Email Address",
            "country": "Country",
            "timezone": "Timezone",
            "birth_year": "Year of Birth",
            "gender": "Gender",
            "unit_preference": "Unit Preference",
            "trainer": "Trainer",
            "power_meter": "Power Meter",
            "dual_recording": "Dual Recording",
            "heartrate_monitor": "Heart Rate Monitor",
            "strava_profile": "Strava Profile",
            "tpv_profile_url": "TrainingPeaks Virtual",
            "agree_privacy": "Privacy Policy",
            "agree_tos": "Terms of Service",
            "applicant_notes": "Additional Notes",
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form and populate dynamic choices.

        Args:
            *args: Positional arguments passed to parent.
            **kwargs: Keyword arguments passed to parent.

        """
        super().__init__(*args, **kwargs)

        # Populate modal form fields from existing modal_form_data
        if self.instance and self.instance.pk and self.instance.modal_form_data:
            modal_data = self.instance.modal_form_data
            self.fields["how_heard"].initial = modal_data.get("how_heard", "")
            self.fields["reasons"].initial = modal_data.get("reasons", [])
            self.fields["know_someone"].initial = modal_data.get("know_someone", "")
            self.fields["platforms"].initial = modal_data.get("platforms", [])
            self.fields["race_series"].initial = modal_data.get("race_series", [])
            self.fields["other_race_series"].initial = modal_data.get("other_race_series", "")

        # Update gender field to show placeholder when empty
        if "gender" in self.fields:
            self.fields["gender"].empty_label = "Select gender..."

        # Update unit_preference field to show placeholder when empty
        if "unit_preference" in self.fields:
            self.fields["unit_preference"].empty_label = "Select preference..."

        # Update dual_recording field to show placeholder when empty
        if "dual_recording" in self.fields:
            self.fields["dual_recording"].empty_label = "Select option..."

        # Populate trainer choices from Constance
        if "trainer" in self.fields:
            trainer_options = json.loads(config.TRAINER_OPTIONS)
            trainer_choices = [("", "Select trainer...")] + [(opt, opt) for opt in trainer_options]
            self.fields["trainer"].widget.choices = trainer_choices

        # Populate powermeter choices from Constance
        if "power_meter" in self.fields:
            powermeter_options = json.loads(config.POWERMETER_OPTIONS)
            powermeter_choices = [("", "None")] + [(opt, opt) for opt in powermeter_options]
            self.fields["power_meter"].widget.choices = powermeter_choices

        # Populate heartrate_monitor choices from Constance
        if "heartrate_monitor" in self.fields:
            hrm_options = json.loads(config.HEARTRATE_MONITOR_OPTIONS)
            hrm_choices = [("", "None")] + [(opt, opt) for opt in hrm_options]
            self.fields["heartrate_monitor"].widget.choices = hrm_choices

        # Make email field required
        if "email" in self.fields:
            self.fields["email"].required = True

    def clean_birth_year(self) -> int | None:
        """Validate birth year is in reasonable range.

        Returns:
            The validated birth year.

        Raises:
            forms.ValidationError: If birth year is outside valid range.

        """
        birth_year = self.cleaned_data.get("birth_year")
        if birth_year:
            current_year = date.today().year
            if birth_year < 1900:
                logfire.warning(
                    "MembershipApplicationApplicantForm birth year validation failed",
                    application_id=str(self.instance.pk) if self.instance and self.instance.pk else None,
                    submitted_value=birth_year,
                    error_reason="too_old",
                )
                raise forms.ValidationError("Birth year must be 1900 or later.")
            if birth_year > current_year - 13:
                logfire.warning(
                    "MembershipApplicationApplicantForm birth year validation failed",
                    application_id=str(self.instance.pk) if self.instance and self.instance.pk else None,
                    submitted_value=birth_year,
                    error_reason="too_young",
                )
                raise forms.ValidationError("You must be at least 13 years old.")
        return birth_year

    def clean(self):
        """Validate that required agreements are checked.

        Returns:
            The cleaned data.

        """
        cleaned_data = super().clean()

        missing_agreements = []
        if not cleaned_data.get("agree_privacy"):
            self.add_error("agree_privacy", "You must agree to the privacy policy.")
            missing_agreements.append("privacy_policy")
        if not cleaned_data.get("agree_tos"):
            self.add_error("agree_tos", "You must agree to the terms of service.")
            missing_agreements.append("terms_of_service")

        if missing_agreements:
            logfire.warning(
                "MembershipApplicationApplicantForm agreement validation failed",
                application_id=str(self.instance.pk) if self.instance and self.instance.pk else None,
                missing_agreements=missing_agreements,
            )

        return cleaned_data

    def save(self, commit: bool = True):
        """Save form and store modal form fields in modal_form_data.

        Args:
            commit: Whether to save the instance to the database.

        Returns:
            The saved MembershipApplication instance.

        """
        instance = super().save(commit=False)

        # Update modal_form_data with the form field values
        modal_data = instance.modal_form_data or {}
        modal_data["how_heard"] = self.cleaned_data.get("how_heard", "")
        modal_data["reasons"] = self.cleaned_data.get("reasons", [])
        modal_data["know_someone"] = self.cleaned_data.get("know_someone", "")
        modal_data["platforms"] = self.cleaned_data.get("platforms", [])
        modal_data["race_series"] = self.cleaned_data.get("race_series", [])
        modal_data["other_race_series"] = self.cleaned_data.get("other_race_series", "")
        instance.modal_form_data = modal_data

        if commit:
            instance.save()
        return instance


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


class ApplicationZwiftVerificationForm(forms.Form):
    """Form for verifying Zwift account credentials in membership applications."""

    zwift_username = forms.EmailField(
        label="Zwift Email",
        widget=forms.EmailInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "your.email@example.com",
                "autocomplete": "email",
            }
        ),
        help_text="The email you use to log into Zwift",
    )
    zwift_password = forms.CharField(
        label="Zwift Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "••••••••",
                "autocomplete": "current-password",
            }
        ),
        help_text="Your Zwift account password (not stored)",
    )
