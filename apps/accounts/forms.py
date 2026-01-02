"""Forms for accounts app."""

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
            "youtube_channel": "YouTube Channel",
        }


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
