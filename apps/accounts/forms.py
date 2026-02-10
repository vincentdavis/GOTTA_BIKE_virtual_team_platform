"""Forms for accounts app."""

import json
from datetime import date
from typing import ClassVar
from zoneinfo import available_timezones

import logfire
from constance import config
from django import forms
from django_countries.widgets import CountrySelectWidget

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

    # Explicit ChoiceField to prevent Django's BooleanField.to_python from
    # converting strings before clean_dual_recording can process them.
    dual_recording = forms.ChoiceField(
        choices=[("", "Select..."), ("True", "Yes"), ("False", "No")],
        required=False,
        label="Dual Recording",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    # Fields that are required for profile completion
    REQUIRED_FIELDS: ClassVar[list[str]] = [
        "first_name",
        "last_name",
        "birth_year",
        "gender",
        "timezone",
        "country",
        "trainer",
        "heartrate_monitor",
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
            # Social Accounts
            "strava_url",
            "garmin_url",
            "tpv_profile_url",
            "youtube_channel",
            "twitch_channel",
            "instagram_url",
            "facebook_url",
            "bluesky_url",
            "tiktok_url",
            "twitter_url",
            "mastodon_url",
            # Training Equipment
            "trainer",
            "powermeter",
            "dual_recording",
            "heartrate_monitor",
            # Emergency Contact
            "emergency_contact_name",
            "emergency_contact_relation",
            "emergency_contact_email",
            "emergency_contact_phone",
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
            "twitch_channel": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://twitch.tv/yourchannel",
                }
            ),
            "instagram_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://instagram.com/yourprofile",
                }
            ),
            "facebook_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://facebook.com/yourprofile",
                }
            ),
            "twitter_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://x.com/yourhandle",
                }
            ),
            "tiktok_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://tiktok.com/@yourhandle",
                }
            ),
            "bluesky_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://bsky.app/profile/yourhandle",
                }
            ),
            "mastodon_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://mastodon.social/@yourhandle",
                }
            ),
            "strava_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://www.strava.com/athletes/1234",
                }
            ),
            "garmin_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://connect.garmin.com/modern/profile/username",
                }
            ),
            "tpv_profile_url": forms.URLInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "https://tpvirtualhub.com/profile/...",
                }
            ),
            "trainer": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "powermeter": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "heartrate_monitor": forms.Select(
                attrs={
                    "class": "select select-bordered w-full",
                }
            ),
            "emergency_contact_name": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Full name",
                }
            ),
            "emergency_contact_relation": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "e.g., spouse, parent, friend",
                }
            ),
            "emergency_contact_email": forms.EmailInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "email@example.com",
                }
            ),
            "emergency_contact_phone": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "+1 555-123-4567",
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
            # Social Accounts
            "strava_url": "Strava",
            "garmin_url": "Garmin Connect",
            "tpv_profile_url": "TrainingPeaks Virtual",
            "youtube_channel": "YouTube",
            "twitch_channel": "Twitch",
            "instagram_url": "Instagram",
            "facebook_url": "Facebook",
            "bluesky_url": "BlueSky",
            "tiktok_url": "TikTok",
            "twitter_url": "Twitter/X",
            "mastodon_url": "Mastodon",
            # Training Equipment
            "trainer": "Trainer",
            "powermeter": "Powermeter",
            "dual_recording": "Dual Recording",
            "heartrate_monitor": "Heart Rate Monitor",
            # Emergency Contact
            "emergency_contact_name": "Contact Name",
            "emergency_contact_relation": "Relationship",
            "emergency_contact_email": "Contact Email",
            "emergency_contact_phone": "Contact Phone",
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

        # Populate trainer choices from Constance
        if "trainer" in self.fields:
            trainer_options = json.loads(config.TRAINER_OPTIONS)
            trainer_choices = [("", "Select trainer...")] + [(opt, opt) for opt in trainer_options]
            self.fields["trainer"].widget.choices = trainer_choices

        # Populate powermeter choices from Constance
        if "powermeter" in self.fields:
            powermeter_options = json.loads(config.POWERMETER_OPTIONS)
            powermeter_choices = [("", "None")] + [(opt, opt) for opt in powermeter_options]
            self.fields["powermeter"].widget.choices = powermeter_choices

        # Populate heartrate_monitor choices from Constance
        if "heartrate_monitor" in self.fields:
            hrm_options = json.loads(config.HEARTRATE_MONITOR_OPTIONS)
            hrm_choices = [("", "None")] + [(opt, opt) for opt in hrm_options]
            self.fields["heartrate_monitor"].widget.choices = hrm_choices

        # Set dual_recording initial value for Select widget
        if "dual_recording" in self.fields and self.instance:
            if self.instance.dual_recording is True:
                self.initial["dual_recording"] = "True"
            elif self.instance.dual_recording is False:
                self.initial["dual_recording"] = "False"
            else:
                self.initial["dual_recording"] = ""

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
                    "ProfileForm birth year validation failed",
                    user_id=self.instance.pk if self.instance else None,
                    submitted_value=birth_year,
                    error_reason="too_old",
                )
                raise forms.ValidationError("Birth year must be 1900 or later.")
            if birth_year > current_year - 13:
                logfire.warning(
                    "ProfileForm birth year validation failed",
                    user_id=self.instance.pk if self.instance else None,
                    submitted_value=birth_year,
                    error_reason="too_young",
                )
                raise forms.ValidationError("You must be at least 13 years old.")
        return birth_year

    def clean_dual_recording(self) -> bool | None:
        """Convert string value to boolean for dual_recording field.

        Returns:
            True, False, or None based on the selected value.

        """
        value = self.cleaned_data.get("dual_recording")
        if value == "True":
            return True
        if value == "False":
            return False
        return None


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
