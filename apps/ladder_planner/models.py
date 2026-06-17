"""Models for the club ladder planner: per-matchup scouting of our team vs an opponent.

A ``LadderMatchup`` pairs our lineup against an opponent lineup on one course.
Each ``LadderRider`` carries a frozen ``zr_data`` snapshot (see
``services.normalize``) so a saved matchup keeps the numbers it was built with;
a refresh re-fetches and overwrites those snapshots on demand.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class CourseProfile(models.TextChoices):
    """Terrain profile of the course, selecting which ZR handicap to apply."""

    FLAT = "flat", "Flat"
    ROLLING = "rolling", "Rolling"
    HILLY = "hilly", "Hilly"
    MOUNTAINOUS = "mountainous", "Mountainous"


class Side(models.TextChoices):
    """Which team a rider races for in the matchup."""

    OURS = "ours", "Our team"
    OPPONENT = "opponent", "Opponent"


class LadderMatchup(models.Model):
    """A saved ladder matchup. The UUID primary key doubles as the share token."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True, help_text="Matchup name")
    our_team_name = models.CharField(max_length=200, blank=True, help_text="Our team name shown on the matchup")
    opponent_team_name = models.CharField(max_length=200, blank=True, help_text="Opponent team name")
    course_name = models.CharField(max_length=200, blank=True, help_text="Course / route name (free text)")
    course_profile = models.CharField(
        max_length=20,
        choices=CourseProfile.choices,
        default=CourseProfile.ROLLING,
        help_text="Terrain profile; selects which ZR handicap drives the projected score",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ladder_matchups",
        help_text="User who created the matchup",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for LadderMatchup."""

        verbose_name = "Ladder Matchup"
        verbose_name_plural = "Ladder Matchups"
        ordering: ClassVar[list[str]] = ["-updated_at"]

    def __str__(self) -> str:
        """Return the matchup name or a fallback.

        Returns:
            Human-readable matchup label.

        """
        return self.name or f"Ladder Matchup {self.pk}"


class LadderRider(models.Model):
    """A rider on one side of a matchup, carrying a frozen ZR data snapshot.

    ``zr_data`` holds the normalized rider dict (see ``services.normalize``) so
    comparison views read one consistent shape for both our riders (sourced from
    ``ZRRider``) and opponents (sourced live from the Zwift Racing API).
    """

    matchup = models.ForeignKey(LadderMatchup, on_delete=models.CASCADE, related_name="riders")
    side = models.CharField(max_length=10, choices=Side.choices, help_text="Which team the rider races for")
    order = models.PositiveIntegerField(default=0, help_text="Display order within the side")
    zwid = models.PositiveIntegerField(help_text="Zwift ID")
    name = models.CharField(max_length=255, help_text="Rider display name")
    zr_data = models.JSONField(default=dict, help_text="Normalized ZR snapshot used by the comparison views")
    is_racing = models.BooleanField(default=True, help_text="Include this rider in comparisons and scoring")
    fetched_at = models.DateTimeField(null=True, blank=True, help_text="When the ZR snapshot was last fetched")

    class Meta:
        """Meta options for LadderRider."""

        verbose_name = "Ladder Rider"
        verbose_name_plural = "Ladder Riders"
        ordering: ClassVar[list[str]] = ["side", "order", "id"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["matchup", "side", "zwid"], name="unique_rider_per_side"),
        ]

    def __str__(self) -> str:
        """Return the rider name.

        Returns:
            Rider display name.

        """
        return self.name


class CachedRider(models.Model):
    """A shared, reusable Zwift Racing snapshot for a non-team (opponent) rider.

    Populated cache-aside from live fetches and the club refresh tasks, and kept
    warm by ``refresh_cached_clubs``. Distinct from ``ZRRider`` (which is "our
    club"): writing opponents here keeps them out of the unified team roster.
    The opponent search reads this table; matches are snapshotted onto
    ``LadderRider`` at add time.
    """

    class Source(models.TextChoices):
        """Where the cached snapshot came from."""

        RIDER = "rider", "Rider fetch"
        CLUB = "club", "Club refresh"

    zwid = models.PositiveIntegerField(unique=True, help_text="Zwift ID")
    name = models.CharField(max_length=255, db_index=True, help_text="Rider display name")
    club_id = models.PositiveIntegerField(null=True, blank=True, db_index=True, help_text="Club ID")
    club_name = models.CharField(max_length=255, blank=True, help_text="Club name")
    zr_data = models.JSONField(default=dict, help_text="Normalized ZR snapshot (see services.normalize)")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.RIDER, help_text="Snapshot origin")
    fetched_at = models.DateTimeField(help_text="When this snapshot was fetched from ZR")

    class Meta:
        """Meta options for CachedRider."""

        verbose_name = "Cached Rider"
        verbose_name_plural = "Cached Riders"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return the rider name and zwid.

        Returns:
            Human-readable label.

        """
        return f"{self.name} ({self.zwid})"


class CachedClub(models.Model):
    """A club whose roster we cache, driving the periodic background refresh.

    A club becomes tracked when one of its riders is fetched. ``refresh_cached_clubs``
    re-pulls due, in-use clubs on a cadence so the opponent search stays current.
    """

    club_id = models.PositiveIntegerField(unique=True, help_text="Club ID")
    name = models.CharField(max_length=255, blank=True, help_text="Club name")
    rider_count = models.PositiveIntegerField(default=0, help_text="Riders cached at last refresh")
    last_refreshed_at = models.DateTimeField(null=True, blank=True, help_text="When the club was last refreshed")
    auto_refresh = models.BooleanField(default=True, help_text="Include in the periodic background refresh")
    last_error = models.CharField(max_length=300, blank=True, help_text="Error from the last refresh, if any")

    class Meta:
        """Meta options for CachedClub."""

        verbose_name = "Cached Club"
        verbose_name_plural = "Cached Clubs"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return the club name and id.

        Returns:
            Human-readable label.

        """
        return f"{self.name or 'Club'} ({self.club_id})"
