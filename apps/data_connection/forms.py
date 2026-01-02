"""Forms for data_connection app."""

from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.data_connection.models import DataConnection


class DataConnectionForm(forms.ModelForm):
    """Form for creating and editing DataConnection records."""

    # Option to create a new Google Sheet
    create_new_sheet = forms.BooleanField(
        required=False,
        initial=True,
        label="Create new Google Sheet",
        widget=forms.CheckboxInput(attrs={"class": "checkbox", "id": "id_create_new_sheet"}),
        help_text="Create a new Google Sheet instead of using an existing one",
    )

    # Multi-select checkboxes for field selection
    user_fields = forms.MultipleChoiceField(
        choices=DataConnection.USER_FIELDS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="User Fields",
    )
    zwiftpower_fields = forms.MultipleChoiceField(
        choices=DataConnection.ZWIFTPOWER_FIELDS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="ZwiftPower Fields",
    )
    zwiftracing_fields = forms.MultipleChoiceField(
        choices=DataConnection.ZWIFTRACING_FIELDS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Zwift Racing Fields",
    )

    # Filter fields
    filter_gender = forms.ChoiceField(
        choices=DataConnection.GENDER_CHOICES,
        required=False,
        label="Gender",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )
    filter_zp_div = forms.ChoiceField(
        choices=DataConnection.ZP_DIVISION_CHOICES,
        required=False,
        label="ZP Division",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )
    filter_zp_divw = forms.ChoiceField(
        choices=DataConnection.ZP_DIVISION_CHOICES,
        required=False,
        label="ZP Women's Division",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )
    filter_zp_skill_race_min = forms.IntegerField(
        required=False,
        label="ZP Skill Race (min)",
        widget=forms.NumberInput(attrs={"class": "input input-bordered w-full", "placeholder": "Min"}),
    )
    filter_zr_rating_min = forms.DecimalField(
        required=False,
        label="ZR Current Rating (min)",
        widget=forms.NumberInput(attrs={"class": "input input-bordered w-full", "placeholder": "Min", "step": "0.01"}),
    )
    filter_zr_rating_max = forms.DecimalField(
        required=False,
        label="ZR Current Rating (max)",
        widget=forms.NumberInput(attrs={"class": "input input-bordered w-full", "placeholder": "Max", "step": "0.01"}),
    )
    filter_zr_phenotype = forms.ChoiceField(
        choices=DataConnection.ZR_PHENOTYPE_CHOICES,
        required=False,
        label="ZR Phenotype",
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    class Meta:
        """Meta options for DataConnectionForm."""

        model = DataConnection
        fields = ["title", "description", "spreadsheet_url", "data_sheet", "date_expires"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "description": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
            "spreadsheet_url": forms.URLInput(attrs={"class": "input input-bordered w-full"}),
            "data_sheet": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "date_expires": forms.DateTimeInput(
                attrs={"class": "input input-bordered w-full", "type": "datetime-local"}
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        """Initialize form with current field selections.

        Args:
            *args: Positional arguments passed to parent.
            **kwargs: Keyword arguments passed to parent.

        """
        super().__init__(*args, **kwargs)

        # Make spreadsheet_url not required (will be validated in clean())
        self.fields["spreadsheet_url"].required = False

        # Set default expiry to 1 year from now for new records
        if not self.instance.pk:
            self.fields["date_expires"].initial = timezone.now() + timedelta(days=365)

        # Populate field selections from existing data
        if self.instance.pk and self.instance.selected_fields:
            user_field_keys = [f[0] for f in DataConnection.USER_FIELDS]
            zp_field_keys = [f[0] for f in DataConnection.ZWIFTPOWER_FIELDS]
            zr_field_keys = [f[0] for f in DataConnection.ZWIFTRACING_FIELDS]

            self.fields["user_fields"].initial = [
                f for f in self.instance.selected_fields if f in user_field_keys
            ]
            self.fields["zwiftpower_fields"].initial = [
                f for f in self.instance.selected_fields if f in zp_field_keys
            ]
            self.fields["zwiftracing_fields"].initial = [
                f for f in self.instance.selected_fields if f in zr_field_keys
            ]

        # Populate filter fields from existing data
        if self.instance.pk and self.instance.filters:
            filters = self.instance.filters
            self.fields["filter_gender"].initial = filters.get("gender", "")
            self.fields["filter_zp_div"].initial = filters.get("zp_div", "")
            self.fields["filter_zp_divw"].initial = filters.get("zp_divw", "")
            self.fields["filter_zp_skill_race_min"].initial = filters.get("zp_skill_race_min")
            self.fields["filter_zr_rating_min"].initial = filters.get("zr_rating_min")
            self.fields["filter_zr_rating_max"].initial = filters.get("zr_rating_max")
            self.fields["filter_zr_phenotype"].initial = filters.get("zr_phenotype", "")

    def clean(self) -> dict:
        """Validate form data.

        Returns:
            Cleaned data dictionary.

        """
        cleaned_data = super().clean()
        create_new_sheet = cleaned_data.get("create_new_sheet")
        spreadsheet_url = cleaned_data.get("spreadsheet_url")

        # If not creating a new sheet, URL is required
        if not create_new_sheet and not spreadsheet_url:
            self.add_error("spreadsheet_url", "Spreadsheet URL is required when not creating a new sheet.")

        return cleaned_data

    def save(self, commit: bool = True) -> DataConnection:
        """Save form and combine selected fields.

        Args:
            commit: Whether to save to database.

        Returns:
            The saved DataConnection instance.

        """
        instance = super().save(commit=False)

        # Combine all selected fields into one list
        selected = []
        selected.extend(self.cleaned_data.get("user_fields", []))
        selected.extend(self.cleaned_data.get("zwiftpower_fields", []))
        selected.extend(self.cleaned_data.get("zwiftracing_fields", []))
        instance.selected_fields = selected

        # Build filters dict (only include non-empty values)
        filters = {}
        if self.cleaned_data.get("filter_gender"):
            filters["gender"] = self.cleaned_data["filter_gender"]
        if self.cleaned_data.get("filter_zp_div"):
            filters["zp_div"] = self.cleaned_data["filter_zp_div"]
        if self.cleaned_data.get("filter_zp_divw"):
            filters["zp_divw"] = self.cleaned_data["filter_zp_divw"]
        if self.cleaned_data.get("filter_zp_skill_race_min") is not None:
            filters["zp_skill_race_min"] = self.cleaned_data["filter_zp_skill_race_min"]
        if self.cleaned_data.get("filter_zr_rating_min") is not None:
            filters["zr_rating_min"] = float(self.cleaned_data["filter_zr_rating_min"])
        if self.cleaned_data.get("filter_zr_rating_max") is not None:
            filters["zr_rating_max"] = float(self.cleaned_data["filter_zr_rating_max"])
        if self.cleaned_data.get("filter_zr_phenotype"):
            filters["zr_phenotype"] = self.cleaned_data["filter_zr_phenotype"]
        instance.filters = filters

        if commit:
            instance.save()

        return instance


class DataConnectionFilterForm(forms.Form):
    """Form for filtering DataConnection list."""

    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Search title or description...",
        }),
    )
    show_expired = forms.BooleanField(
        required=False,
        initial=False,
        label="Show expired",
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )
    sort_by = forms.ChoiceField(
        required=False,
        choices=[
            ("-date_created", "Newest first"),
            ("date_created", "Oldest first"),
            ("-date_expires", "Expires latest"),
            ("date_expires", "Expires soonest"),
            ("-date_last_synced", "Recently synced"),
        ],
        initial="-date_created",
        widget=forms.Select(attrs={"class": "select select-bordered"}),
    )
