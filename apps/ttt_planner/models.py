"""Models for the TTT planner: routes, plans, and per-plan riders."""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.ttt_planner import terrain
from apps.ttt_planner.worlds import world_choices

# whatsonzwift world slugs that don't match slugify(world).
_WOZ_WORLD_OVERRIDES = {"bologna": "bologna-tt"}


class Route(models.Model):
    """A Zwift route a TTT can be planned on.

    Seeded with popular WTRL TTT routes; admins can add more in Django admin.
    """

    name = models.CharField(max_length=200, help_text="Route name")
    world = models.CharField(max_length=100, blank=True, choices=world_choices, help_text="Zwift world")
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, help_text="Route distance in km")
    elevation_m = models.PositiveIntegerField(default=0, help_text="Total elevation gain in metres")
    zwift_route_id = models.CharField(max_length=50, blank=True, help_text="Zwift route identifier, if known")
    is_active = models.BooleanField(default=True, help_text="Show in the route picker")
    lead_in_distance_km = models.DecimalField(
        max_digits=6, decimal_places=2, default=0, help_text="Lead-in distance in km (before the lap starts)"
    )
    lead_in_elevation_m = models.PositiveIntegerField(default=0, help_text="Lead-in elevation gain in metres")
    supports_laps = models.BooleanField(default=False, help_text="Route can be ridden as multiple laps")
    recommended_laps = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Club Ladder recommended number of laps"
    )
    zwiftinsider_url = models.URLField(blank=True, help_text="ZwiftInsider route page URL")
    segments = models.ManyToManyField(
        "Segment", blank=True, related_name="routes", help_text="Climbs and sprints on this route"
    )

    class Meta:
        """Meta options for Route."""

        verbose_name = "TTT Route"
        verbose_name_plural = "TTT Routes"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return the route name with distance.

        Returns:
            Human-readable route label.

        """
        return f"{self.name} ({self.distance_km} km)"

    @property
    def whatsonzwift_url(self) -> str:
        """Build a best-effort whatsonzwift.com link from the world + name.

        The route slug is derived from the route name (not ``zwift_route_id``,
        which doesn't always match whatsonzwift). A small override map handles
        worlds whose slug differs (e.g. Bologna → bologna-tt). Coverage is high
        but not guaranteed for every route.

        Returns:
            The whatsonzwift URL, or empty string if world/name is missing.

        """
        world_slug = slugify(self.world)
        route_slug = slugify(self.name)
        if not world_slug or not route_slug:
            return ""
        world_slug = _WOZ_WORLD_OVERRIDES.get(world_slug, world_slug)
        return f"https://whatsonzwift.com/world/{world_slug}/route/{route_slug}"


class RouteGpx(models.Model):
    """An uploaded GPX track for a route.

    A route can have several (different spawn points / lead-ins), so this is a FK.
    Distance / elevation / terrain are parsed from the file on upload; the start
    point and lead-in are captured as free-text notes.
    """

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="gpx_files")
    label = models.CharField(max_length=200, blank=True, help_text="Label for this track (e.g. spawn point / lead-in)")
    file = models.FileField(upload_to="route_gpx/", help_text="The uploaded .gpx file")
    notes = models.TextField(blank=True, help_text="Free-text notes: start point and lead-in")
    distance_km = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, help_text="Distance parsed from the GPX (km)"
    )
    elevation_m = models.PositiveIntegerField(null=True, blank=True, help_text="Elevation gain parsed from the GPX (m)")
    terrain = models.CharField(max_length=20, blank=True, help_text="Terrain type derived from the parsed GPX")
    profile = models.JSONField(
        default=list, blank=True, help_text="Downsampled elevation profile: [[distance_km, elevation_m], ...]"
    )
    point_count = models.PositiveIntegerField(default=0, help_text="Number of track points parsed")
    parse_error = models.CharField(max_length=300, blank=True, help_text="Error from parsing the GPX, if any")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta options for RouteGpx."""

        verbose_name = "Route GPX"
        verbose_name_plural = "Route GPX files"
        ordering: ClassVar[list[str]] = ["route", "label", "id"]

    def __str__(self) -> str:
        """Return a label for the GPX file.

        Returns:
            Human-readable label.

        """
        return f"{self.route.name} — {self.label or self.file.name}"


class Segment(models.Model):
    """A timed Zwift segment (climb or sprint) that routes can contain.

    Modelled standalone and linked to routes via ``Route.segments`` (many-to-many),
    since the same segment (e.g. the Epic KOM) appears on many routes.
    """

    class SegmentType(models.TextChoices):
        """Climb, Sprint, or a generic timed segment."""

        CLIMB = "climb", "Climb"
        SPRINT = "sprint", "Sprint"
        SEGMENT = "segment", "Segment"

    class Direction(models.TextChoices):
        """Which way the segment is ridden."""

        FORWARD = "forward", "Forward"
        REVERSE = "reverse", "Reverse"

    segment_type = models.CharField(
        max_length=10, choices=SegmentType.choices, help_text="Climb, Sprint, or generic Segment"
    )
    direction = models.CharField(
        max_length=10, choices=Direction.choices, blank=True, help_text="Forward or Reverse (blank if single-direction)"
    )
    name = models.CharField(max_length=200, help_text="Segment name")
    category = models.CharField(max_length=5, blank=True, help_text="Climb category (HC, 1-4) if applicable")
    notes = models.TextField(blank=True, help_text="Free-text notes")
    length_m = models.PositiveIntegerField(default=0, help_text="Segment length in metres")
    elevation_m = models.PositiveIntegerField(default=0, help_text="Segment elevation gain in metres")
    grade_pct = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, help_text="Average grade %"
    )
    world = models.CharField(
        max_length=100, blank=True, choices=world_choices, help_text="Zwift world"
    )
    strava_url = models.URLField(blank=True, help_text="Strava segment URL")
    zwiftinsider_url = models.URLField(blank=True, help_text="ZwiftInsider segment URL")
    whatsonzwift_url = models.URLField(blank=True, help_text="whatsonzwift.com segment URL")

    class Meta:
        """Meta options for Segment."""

        verbose_name = "Segment"
        verbose_name_plural = "Segments"
        ordering: ClassVar[list[str]] = ["world", "name", "direction"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["name", "world", "direction"], name="unique_segment_name_world_direction"),
        ]

    def __str__(self) -> str:
        """Return a label for the segment.

        Returns:
            Human-readable label with its type and direction.

        """
        direction = f", {self.get_direction_display()}" if self.direction else ""
        return f"{self.name} ({self.get_segment_type_display()}{direction})"


class PowerUp(models.Model):
    """A Zwift PowerUp, shown on the routes reference page.

    Shared reference data that race-verified members can curate (same pattern as
    ``Route`` / ``Segment``). The ``excluded_from_ladder`` flag marks PowerUps
    that do not count for the Club Ladder (the XP bonuses and Boost).
    """

    name = models.CharField(max_length=100, unique=True, help_text="PowerUp name (e.g. Feather)")
    aka = models.CharField(max_length=100, blank=True, help_text="Alternate name (e.g. Lightweight)")
    slug = models.SlugField(max_length=120, unique=True, blank=True, help_text="URL/icon slug (auto from name)")
    effect = models.TextField(blank=True, help_text="What the PowerUp does, including duration")
    duration_seconds = models.PositiveIntegerField(default=0, help_text="Effect duration in seconds (0 = instant/none)")
    event_only = models.BooleanField(default=False, help_text="Only available in events")
    excluded_from_ladder = models.BooleanField(default=False, help_text="Does not count for the Club Ladder")
    icon = models.ImageField(upload_to="powerup_icons/", blank=True, help_text="PowerUp icon image")
    order = models.PositiveSmallIntegerField(default=0, help_text="Display order on the routes page")
    is_active = models.BooleanField(default=True, help_text="Show in the PowerUps list")

    class Meta:
        """Meta options for PowerUp."""

        verbose_name = "PowerUp"
        verbose_name_plural = "PowerUps"
        ordering: ClassVar[list[str]] = ["order", "name"]

    def __str__(self) -> str:
        """Return the PowerUp name.

        Returns:
            The PowerUp name.

        """
        return self.name

    def save(self, *args: object, **kwargs: object) -> None:
        """Populate ``slug`` from ``name`` when missing, then save.

        Args:
            *args: Positional args passed through to ``Model.save``.
            **kwargs: Keyword args passed through to ``Model.save``.

        """
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class TttPlan(models.Model):
    """A saved TTT plan. The UUID primary key doubles as the share token."""

    class EventType(models.TextChoices):
        """The kind of event this plan targets."""

        ZRL = "zrl", "ZRL"
        DRS = "drs", "DRS"
        WTRL_TTT = "wtrl_ttt", "WTRL TTT"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True, help_text="Plan name")
    team_name = models.CharField(max_length=200, blank=True, help_text="Team name shown on the plan")
    event_type = models.CharField(
        max_length=20, blank=True, default="", choices=EventType.choices, help_text="Event this plan targets"
    )
    route = models.ForeignKey(
        Route, on_delete=models.SET_NULL, null=True, blank=True, related_name="plans", help_text="Selected route"
    )
    course_name = models.CharField(max_length=200, blank=True, help_text="Course / route name (free text)")
    course_type = models.CharField(
        max_length=20,
        blank=True,
        choices=terrain.TERRAIN_CHOICES,
        help_text="Terrain type (informational; prefilled from the route)",
    )
    target_speed_kph = models.DecimalField(
        max_digits=5, decimal_places=1, default=40.0, help_text="Target flat speed in km/h"
    )
    target_if = models.FloatField(
        default=0.95, help_text="Target intensity factor used by Calculate / Auto-balance (e.g. 0.95)"
    )
    draft_savings = models.JSONField(
        default=list,
        blank=True,
        help_text="Per-plan aero draft savings fractions by wheel position, e.g. [0.0, 0.233, 0.30]. "
        "Index 0 is the front rider (no draft). Empty list uses the global TTT_DRAFT_SAVINGS default.",
    )
    cda_coef = models.FloatField(
        null=True,
        blank=True,
        help_text="Per-plan aero CdA coefficient. Blank uses the global TTT_CDA_COEF default.",
    )

    class GopherStatus(models.TextChoices):
        """Status of the most recent zwiftgopher optimize run."""

        NONE = "", "Not run"
        PENDING = "pending", "Running"
        DONE = "done", "Done"
        ERROR = "error", "Error"

    zwiftgopher_status = models.CharField(
        max_length=10, blank=True, default="", choices=GopherStatus.choices, help_text="zwiftgopher run status"
    )
    zwiftgopher_result = models.JSONField(
        null=True, blank=True, help_text="Normalized result from the last zwiftgopher optimize run"
    )
    zwiftgopher_request = models.JSONField(
        null=True, blank=True, help_text="Raw request body sent to the zwiftgopher optimize API"
    )
    zwiftgopher_raw_response = models.JSONField(
        null=True, blank=True, help_text="Raw JSON response from the zwiftgopher optimize API"
    )
    zwiftgopher_error = models.CharField(max_length=300, blank=True, help_text="Error from the last zwiftgopher run")
    zwiftgopher_fetched_at = models.DateTimeField(
        null=True, blank=True, help_text="When the last zwiftgopher result was fetched"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ttt_plans",
        help_text="User who created the plan",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for TttPlan."""

        verbose_name = "TTT Plan"
        verbose_name_plural = "TTT Plans"
        ordering: ClassVar[list[str]] = ["-updated_at"]

    def __str__(self) -> str:
        """Return the plan name or a fallback.

        Returns:
            Human-readable plan label.

        """
        return self.name or f"TTT Plan {self.pk}"


class PlanRider(models.Model):
    """A rider on a plan, in pull order.

    Weight/height/FTP are snapshotted at add time so later changes to team data
    do not mutate a saved plan.
    """

    plan = models.ForeignKey(TttPlan, on_delete=models.CASCADE, related_name="riders")
    order = models.PositiveIntegerField(default=0, help_text="Pull order (0-based)")
    zwid = models.PositiveIntegerField(null=True, blank=True, help_text="Zwift ID, if linked to team data")
    name = models.CharField(max_length=200, help_text="Rider display name")
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, help_text="Weight in kg")
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Height in cm")
    ftp_w = models.PositiveSmallIntegerField(null=True, blank=True, help_text="FTP in watts")
    pull_power_w = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Override pull power in watts")
    pull_duration_s = models.PositiveIntegerField(default=60, help_text="Pull duration in seconds")
    zero_pull = models.BooleanField(default=False, help_text="Rider takes no pulls (sits in for recovery)")

    class Meta:
        """Meta options for PlanRider."""

        verbose_name = "Plan Rider"
        verbose_name_plural = "Plan Riders"
        ordering: ClassVar[list[str]] = ["order", "id"]

    def __str__(self) -> str:
        """Return the rider name.

        Returns:
            Rider display name.

        """
        return self.name
