"""Canonical Zwift worlds / routes / segments, synced from Zwift Speed Lab.

These are the *reference* dataset (source of truth for what routes and segments exist
in Zwift), distinct from ``apps.ttt_planner.Route`` / ``Segment`` which stay for the
TTT and Ladder planners (curated vELO2 weights, recommended laps, plan FKs). The two
sets link by ``name`` + ``world`` when a planner needs canonical detail.

Rows are populated by ``apps.zwift_data.services.sync.sync_dataset`` from the
``/api/data/all.zip`` bundle. Per-route elevation/GPS profiles are **not** stored here
— they live as JSON in object storage and are served through ``catalog.py`` (see the
``bucket file + memory cache`` decision).
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models


class ZwiftWorld(models.Model):
    """A Zwift world (Watopia, London, …). ``world_id`` is Zwift's numeric id."""

    world_id = models.PositiveIntegerField(unique=True, help_text="Zwift numeric world id")
    name = models.CharField(max_length=100, unique=True, help_text="World display name")
    route_count = models.PositiveIntegerField(default=0, help_text="Routes in this world (dataset)")
    segment_count = models.PositiveIntegerField(default=0, help_text="Segments in this world (dataset)")

    class Meta:
        """Meta options for ZwiftWorld."""

        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = "Zwift world"

    def __str__(self) -> str:
        """Return the world name.

        Returns:
            The world display name.

        """
        return self.name


class ZwiftRoute(models.Model):
    """One Zwift route. Keyed by ``(world_id, name_hash)`` — the join to profiles/GPX."""

    class Sport(models.TextChoices):
        """Whether the route is a cycling or running route."""

        CYCLING = "cycling", "Cycling"
        RUNNING = "running", "Running"

    name = models.CharField(max_length=200, help_text="Route name")
    world = models.CharField(max_length=100, help_text="Zwift world name")
    world_id = models.PositiveIntegerField(help_text="Zwift numeric world id")
    name_hash = models.CharField(max_length=32, help_text="Route id within its world (join key)")
    sport = models.CharField(max_length=10, choices=Sport.choices, default=Sport.CYCLING)
    distance_km = models.FloatField(default=0.0, help_text="Route length, excludes lead-in")
    ascent_m = models.PositiveIntegerField(default=0, help_text="Elevation gain, excludes lead-in")
    avg_gradient_pct = models.FloatField(default=0.0, help_text="ascent / distance")
    leadin_km = models.FloatField(default=0.0, help_text="Neutral roll-in distance before the route")
    leadin_ascent_m = models.PositiveIntegerField(default=0, help_text="Lead-in elevation gain")
    supports_tt = models.BooleanField(default=False, help_text="Can be ridden in time-trial mode")
    event_only = models.BooleanField(default=False, help_text="Only available inside an event")
    level_locked = models.PositiveIntegerField(default=0, help_text="Rider level required (0 = none)")

    class Meta:
        """Meta options for ZwiftRoute."""

        ordering: ClassVar[list[str]] = ["world", "name"]
        verbose_name = "Zwift route"
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["world_id", "name_hash"], name="uniq_zwiftroute_world_hash"),
        ]
        indexes: ClassVar[list] = [models.Index(fields=["world", "name"])]

    def __str__(self) -> str:
        """Return a label for the route.

        Returns:
            The route name with its world.

        """
        return f"{self.name} ({self.world})"

    @property
    def total_distance_km(self) -> float:
        """Route distance including the lead-in.

        Returns:
            Distance in km, lead-in included, rounded to 2 dp.

        """
        return round(self.distance_km + self.leadin_km, 2)


class ZwiftSegment(models.Model):
    """A Zwift live segment (sprint / KOM / climb / lap). ``segment_id`` is signed 64-bit."""

    class SegmentType(models.TextChoices):
        """The kind of live segment."""

        SPRINT = "sprint", "Sprint"
        KOM = "kom", "KOM"
        CLIMB = "climb", "Climb"
        LAP = "lap", "Lap"
        SEGMENT = "segment", "Segment"

    class Direction(models.TextChoices):
        """Which way the segment is ridden."""

        FORWARD = "Forward", "Forward"
        REVERSE = "Reverse", "Reverse"

    # signed 64-bit live-segment id (matches Zwift's leaderboards); can be negative
    segment_id = models.BigIntegerField(unique=True, help_text="Signed 64-bit live-segment id")
    name = models.CharField(max_length=200, blank=True, help_text="Segment name (blank if unresolved)")
    segment_type = models.CharField(max_length=10, choices=SegmentType.choices, default=SegmentType.SEGMENT)
    direction = models.CharField(max_length=10, choices=Direction.choices, blank=True)
    world = models.CharField(max_length=100, help_text="Zwift world name")
    world_id = models.PositiveIntegerField(help_text="Zwift numeric world id")
    course_id = models.PositiveIntegerField(default=0, help_text="Zwift course id")
    road_id = models.IntegerField(default=0, help_text="Zwift road id")
    length_m = models.PositiveIntegerField(default=0, help_text="Segment length in metres")
    ascent_m = models.FloatField(default=0.0, help_text="Elevation gain in metres")
    avg_grade_pct = models.FloatField(default=0.0, help_text="Average grade %")
    max_grade_pct = models.FloatField(default=0.0, help_text="Maximum grade %")
    gives_powerup = models.BooleanField(default=False, help_text="Crossing awards a power-up")
    route_count = models.PositiveIntegerField(default=0, help_text="Distinct routes that cross it")

    class Meta:
        """Meta options for ZwiftSegment."""

        ordering: ClassVar[list[str]] = ["world", "name"]
        verbose_name = "Zwift segment"
        indexes: ClassVar[list] = [models.Index(fields=["world", "segment_type"])]

    def __str__(self) -> str:
        """Return a label for the segment.

        Returns:
            The segment name, or its id when unresolved.

        """
        return self.name or f"Segment {self.segment_id}"

    @property
    def display_name(self) -> str:
        """Name, falling back to the id when unresolved.

        Returns:
            The segment name, or ``"Segment <id>"`` when blank.

        """
        return self.name or f"Segment {self.segment_id}"


class ZwiftDataset(models.Model):
    """Singleton tracking the last synced Speed Lab bundle (version + counts)."""

    SINGLETON_ID = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_ID)
    source_url = models.URLField(help_text="Bundle URL it was fetched from")
    synced_at = models.DateTimeField(null=True, blank=True, help_text="When the last sync completed")
    bundle_last_modified = models.CharField(
        max_length=100, blank=True, help_text="Last-Modified/ETag reported by the source"
    )
    bundle_bytes = models.PositiveBigIntegerField(default=0, help_text="Downloaded bundle size")
    routes_count = models.PositiveIntegerField(default=0)
    segments_count = models.PositiveIntegerField(default=0)
    worlds_count = models.PositiveIntegerField(default=0)
    profiles_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, help_text="Error from the last failed sync")
    syncing = models.BooleanField(default=False, help_text="A sync is currently in progress")

    class Meta:
        """Meta options for ZwiftDataset."""

        verbose_name = "Zwift dataset"
        verbose_name_plural = "Zwift dataset"

    def __str__(self) -> str:
        """Return a summary of the dataset version.

        Returns:
            Route count and last-sync time.

        """
        return f"Zwift dataset ({self.routes_count} routes, synced {self.synced_at or 'never'})"

    @classmethod
    def get(cls) -> ZwiftDataset:
        """Return the singleton row, creating it on first access.

        Returns:
            The :class:`ZwiftDataset` singleton.

        """
        obj, _ = cls.objects.get_or_create(id=cls.SINGLETON_ID)
        return obj
