"""Forms for CMS app."""

import json
from typing import ClassVar

from django import forms
from django.core.exceptions import ValidationError

from apps.cms.models import Page


class PageForm(forms.ModelForm):
    """Form for creating and editing CMS pages."""

    class Meta:
        """Form metadata."""

        model = Page
        fields: ClassVar[list[str]] = [
            "title",
            "slug",
            "status",
            "content",
            "hero_enabled",
            "hero_image",
            "hero_title",
            "hero_subtitle",
            "cards_above",
            "cards_below",
            "show_in_nav",
            "nav_title",
            "nav_order",
            "require_login",
            "require_team_member",
            "meta_description",
        ]
        widgets: ClassVar[dict] = {
            "title": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Page Title"}),
            "slug": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "page-slug"}
            ),
            "status": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "content": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full font-mono",
                    "rows": 15,
                    "placeholder": "Write your content in Markdown format...",
                }
            ),
            "hero_enabled": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "hero_image": forms.ClearableFileInput(attrs={"class": "file-input file-input-bordered w-full"}),
            "hero_title": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Hero Title (optional)"}
            ),
            "hero_subtitle": forms.Textarea(
                attrs={
                    "class": "textarea textarea-bordered w-full",
                    "rows": 3,
                    "placeholder": "Hero subtitle (supports Markdown)",
                }
            ),
            "cards_above": forms.HiddenInput(attrs={"id": "id_cards_above"}),
            "cards_below": forms.HiddenInput(attrs={"id": "id_cards_below"}),
            "show_in_nav": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "nav_title": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Nav Title (optional)"}
            ),
            "nav_order": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "require_login": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "require_team_member": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "meta_description": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Meta description for SEO (max 160 characters)",
                    "maxlength": 160,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize form with JSON field handling."""
        super().__init__(*args, **kwargs)
        # Convert JSON fields to string for display
        if self.instance.pk:
            if self.instance.cards_above:
                self.initial["cards_above"] = json.dumps(self.instance.cards_above, indent=2)
            if self.instance.cards_below:
                self.initial["cards_below"] = json.dumps(self.instance.cards_below, indent=2)

    def clean_cards_above(self) -> list:
        """Validate and parse cards_above JSON.

        Returns:
            Parsed JSON list.

        """
        return self._clean_json_field("cards_above")

    def clean_cards_below(self) -> list:
        """Validate and parse cards_below JSON.

        Returns:
            Parsed JSON list.

        """
        return self._clean_json_field("cards_below")

    def _clean_json_field(self, field_name: str) -> list:
        """Clean a JSON field, parsing string input to list.

        Args:
            field_name: Name of the field to clean.

        Returns:
            Parsed JSON list or empty list.

        Raises:
            ValidationError: If JSON is invalid.

        """
        data = self.cleaned_data.get(field_name)
        if not data:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, str):
            data = data.strip()
            if not data:
                return []
            try:
                parsed = json.loads(data)
                if not isinstance(parsed, list):
                    raise ValidationError("Must be a JSON array.")
                return parsed
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON: {e}") from e
        return []
