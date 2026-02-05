"""Models for Strava club data."""

from typing import ClassVar

from django.db import models


class ClubActivity(models.Model):
    """Strava club activity from the Club Activities API.

    Note: Strava does not provide athlete IDs in club activities for privacy.
    Athletes are identified by first_name and last_name only.
    """

    # Activity identification
    strava_id = models.BigIntegerField(unique=True, help_text="Strava activity ID")

    # Athlete info (no ID available from club activities endpoint)
    athlete_first_name = models.CharField(max_length=255, help_text="Athlete first name")
    athlete_last_name = models.CharField(max_length=255, blank=True, help_text="Athlete last name (initial only)")

    # Activity details
    name = models.CharField(max_length=255, help_text="Activity name")
    sport_type = models.CharField(max_length=50, help_text="Sport type (e.g., Ride, VirtualRide)")
    workout_type = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Workout type code")

    # Metrics
    distance = models.DecimalField(max_digits=12, decimal_places=2, help_text="Distance in meters")
    moving_time = models.PositiveIntegerField(help_text="Moving time in seconds")
    elapsed_time = models.PositiveIntegerField(help_text="Elapsed time in seconds")
    total_elevation_gain = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Elevation gain in meters"
    )

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True, help_text="Record created in database")
    date_modified = models.DateTimeField(auto_now=True, help_text="Record last modified")

    class Meta:
        """Meta options for ClubActivity model."""

        verbose_name = "Club Activity"
        verbose_name_plural = "Club Activities"
        ordering: ClassVar[list[str]] = ["-date_created"]

    def __str__(self) -> str:
        """Return string representation of the activity.

        Returns:
            String with activity name and athlete.

        """
        return f"{self.name} by {self.athlete_first_name}"

    @property
    def distance_km(self) -> float:
        """Return distance in kilometers.

        Returns:
            Distance converted to kilometers.

        """
        return float(self.distance) / 1000

    @property
    def distance_miles(self) -> float:
        """Return distance in miles.

        Returns:
            Distance converted to miles.

        """
        return float(self.distance) / 1609.344

    @property
    def moving_time_formatted(self) -> str:
        """Return moving time as HH:MM:SS string.

        Returns:
            Formatted time string.

        """
        hours, remainder = divmod(self.moving_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def elevation_gain_ft(self) -> float | None:
        """Return elevation gain in feet.

        Returns:
            Elevation converted to feet, or None if no elevation data.

        """
        if self.total_elevation_gain is None:
            return None
        return float(self.total_elevation_gain) * 3.28084
