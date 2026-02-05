"""Admin configuration for club_strava app."""

from typing import ClassVar

from django.contrib import admin

from apps.club_strava.models import ClubActivity


@admin.register(ClubActivity)
class ClubActivityAdmin(admin.ModelAdmin):
    """Admin for ClubActivity model."""

    list_display: ClassVar[list[str]] = [
        "name",
        "athlete_first_name",
        "sport_type",
        "distance_display",
        "moving_time_formatted",
        "date_created",
    ]
    list_filter: ClassVar[list[str]] = ["sport_type", "date_created"]
    search_fields: ClassVar[list[str]] = ["name", "athlete_first_name", "athlete_last_name"]
    readonly_fields: ClassVar[list[str]] = ["strava_id", "date_created", "date_modified"]
    ordering: ClassVar[list[str]] = ["-date_created"]

    @admin.display(description="Distance (km)")
    def distance_display(self, obj: ClubActivity) -> str:
        """Display distance in kilometers.

        Args:
            obj: The ClubActivity instance.

        Returns:
            Formatted distance string.

        """
        return f"{obj.distance_km:.1f}"
