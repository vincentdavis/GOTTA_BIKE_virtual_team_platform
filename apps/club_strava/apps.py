"""App configuration for club_strava."""

from django.apps import AppConfig


class ClubStravaConfig(AppConfig):
    """Configuration for the Club Strava app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.club_strava"
    verbose_name = "Club Strava"
