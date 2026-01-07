"""Forms for accounts app."""

from datetime import date
from typing import ClassVar
from zoneinfo import available_timezones

from django import forms

from apps.accounts.models import User

# Common timezones sorted by region
TIMEZONE_CHOICES = [
    ("", "Select timezone..."),
    *sorted(
        [(tz, tz.replace("_", " ")) for tz in available_timezones() if "/" in tz],
        key=lambda x: x[0],
    ),
]


class ProfileForm(forms.ModelForm):
    """Form for editing user profile."""

    # Fields that are required for profile completion
    REQUIRED_FIELDS: ClassVar[list[str]] = [
        "first_name",
        "last_name",
        "birth_year",
        "gender",
        "timezone",
        "country",
    ]

    class Meta:
        """Meta options for ProfileForm."""

        model = User
        fields: ClassVar[list[str]] = [
            "first_name",
            "last_name",
            "birth_year",
            "email",
            "gender",
            "city",
            "country",
            "timezone",
            "unit_preference",
            "youtube_channel",
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
            "birth_year": forms.NumberInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "e.g., 1990",
                    "min": 1900,
                    "max": 2020,
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Email address",
                }
            ),
            "gender": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "city": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "City",
                }
            ),
            "country": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Country",
                }
            ),
            "timezone": forms.Select(
                choices=TIMEZONE_CHOICES,
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "unit_preference": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "youtube_channel": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://youtube.com/@yourchannel",
                }
            ),
        }
        labels: ClassVar[dict[str, str]] = {
            "first_name": "First Name",
            "last_name": "Last Name",
            "birth_year": "Year of Birth",
            "email": "Email",
            "gender": "Gender",
            "city": "City",
            "country": "Country",
            "timezone": "Timezone",
            "unit_preference": "Unit Preference",
            "youtube_channel": "YouTube Channel",
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form and set required fields.

        Args:
            *args: Positional arguments passed to parent.
            **kwargs: Keyword arguments passed to parent.

        """
        super().__init__(*args, **kwargs)

        # Mark required fields
        for field_name in self.REQUIRED_FIELDS:
            if field_name in self.fields:
                self.fields[field_name].required = True

        # Update gender field to show placeholder when empty
        if "gender" in self.fields:
            self.fields["gender"].empty_label = "Select gender..."

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
                raise forms.ValidationError("Birth year must be 1900 or later.")
            if birth_year > current_year - 13:
                raise forms.ValidationError("You must be at least 13 years old.")
        return birth_year


class ZwiftVerificationForm(forms.Form):
    """Form for verifying Zwift account credentials."""

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
