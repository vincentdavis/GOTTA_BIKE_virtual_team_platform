"""Forms for race-verified users to curate routes and segments."""

from typing import ClassVar

from django import forms

from apps.ttt_planner.models import Route, Segment

_INPUT = {"class": "input input-bordered w-full"}
_SELECT = {"class": "select select-bordered w-full"}
_CHECK = {"class": "checkbox"}
_AREA = {"class": "textarea textarea-bordered w-full", "rows": 3}


class RouteForm(forms.ModelForm):
    """Create/edit a route, including the lead-in, laps, and linked segments."""

    class Meta:
        """Form metadata."""

        model = Route
        fields: ClassVar[list[str]] = [
            "name",
            "world",
            "distance_km",
            "elevation_m",
            "zwift_route_id",
            "lead_in_distance_km",
            "lead_in_elevation_m",
            "supports_laps",
            "recommended_laps",
            "zwiftinsider_url",
            "segments",
            "is_active",
        ]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(attrs=_INPUT),
            "world": forms.Select(attrs=_SELECT),
            "distance_km": forms.NumberInput(attrs={**_INPUT, "step": "0.01"}),
            "elevation_m": forms.NumberInput(attrs=_INPUT),
            "zwift_route_id": forms.TextInput(attrs=_INPUT),
            "lead_in_distance_km": forms.NumberInput(attrs={**_INPUT, "step": "0.01"}),
            "lead_in_elevation_m": forms.NumberInput(attrs=_INPUT),
            "supports_laps": forms.CheckboxInput(attrs=_CHECK),
            "recommended_laps": forms.NumberInput(attrs=_INPUT),
            "zwiftinsider_url": forms.URLInput(attrs=_INPUT),
            "segments": forms.SelectMultiple(attrs={"class": "select select-bordered w-full", "size": "8"}),
            "is_active": forms.CheckboxInput(attrs=_CHECK),
        }


class SegmentForm(forms.ModelForm):
    """Create/edit a climb or sprint segment."""

    class Meta:
        """Form metadata."""

        model = Segment
        fields: ClassVar[list[str]] = [
            "segment_type",
            "direction",
            "name",
            "world",
            "category",
            "length_m",
            "elevation_m",
            "grade_pct",
            "notes",
            "strava_url",
            "zwiftinsider_url",
            "whatsonzwift_url",
        ]
        widgets: ClassVar[dict] = {
            "segment_type": forms.Select(attrs=_SELECT),
            "direction": forms.Select(attrs=_SELECT),
            "name": forms.TextInput(attrs=_INPUT),
            "world": forms.Select(attrs=_SELECT),
            "category": forms.TextInput(attrs=_INPUT),
            "length_m": forms.NumberInput(attrs=_INPUT),
            "elevation_m": forms.NumberInput(attrs=_INPUT),
            "grade_pct": forms.NumberInput(attrs={**_INPUT, "step": "0.1"}),
            "notes": forms.Textarea(attrs=_AREA),
            "strava_url": forms.URLInput(attrs=_INPUT),
            "zwiftinsider_url": forms.URLInput(attrs=_INPUT),
            "whatsonzwift_url": forms.URLInput(attrs=_INPUT),
        }
