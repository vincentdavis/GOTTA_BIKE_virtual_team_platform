"""Forms for race-verified users to curate routes and segments."""

from typing import ClassVar

from django import forms
from django.db.models import Q

from apps.ttt_planner.models import PowerUp, Route, Segment

_INPUT = {"class": "input input-bordered w-full"}
_SELECT = {"class": "select select-bordered w-full"}
_CHECK = {"class": "checkbox"}
_AREA = {"class": "textarea textarea-bordered w-full", "rows": 3}
_FILE = {"class": "file-input file-input-bordered w-full"}


class RouteForm(forms.ModelForm):
    """Create/edit a route, including the lead-in, laps, and linked segments."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Limit the segments picker to this route's world (plus any already linked).

        Without this the picker lists every segment in every world. A route lives
        in one world, so we only offer segments from it — set & save the route's
        world first, then edit to choose segments.
        """
        super().__init__(*args, **kwargs)
        world = getattr(self.instance, "world", "") or ""
        linked_pks = list(self.instance.segments.values_list("pk", flat=True)) if self.instance.pk else []
        if world:
            self.fields["segments"].queryset = Segment.objects.filter(Q(world=world) | Q(pk__in=linked_pks))
        elif linked_pks:
            self.fields["segments"].queryset = Segment.objects.filter(pk__in=linked_pks)
        else:
            self.fields["segments"].queryset = Segment.objects.none()
        self.fields["segments"].help_text = "Climbs & sprints in this route's world (set & save the world first)."

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
            "segments": forms.CheckboxSelectMultiple(),
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


class PowerUpForm(forms.ModelForm):
    """Create/edit a Zwift PowerUp shown on the routes reference page."""

    class Meta:
        """Form metadata."""

        model = PowerUp
        fields: ClassVar[list[str]] = [
            "name",
            "aka",
            "effect",
            "duration_seconds",
            "icon",
            "event_only",
            "excluded_from_ladder",
            "order",
            "is_active",
        ]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(attrs=_INPUT),
            "aka": forms.TextInput(attrs=_INPUT),
            "effect": forms.Textarea(attrs=_AREA),
            "duration_seconds": forms.NumberInput(attrs=_INPUT),
            "icon": forms.ClearableFileInput(attrs=_FILE),
            "event_only": forms.CheckboxInput(attrs=_CHECK),
            "excluded_from_ladder": forms.CheckboxInput(attrs=_CHECK),
            "order": forms.NumberInput(attrs=_INPUT),
            "is_active": forms.CheckboxInput(attrs=_CHECK),
        }
