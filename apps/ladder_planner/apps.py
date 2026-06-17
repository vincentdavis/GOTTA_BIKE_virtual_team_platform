"""App configuration for the ladder planner."""

from django.apps import AppConfig


class LadderPlannerConfig(AppConfig):
    """Configuration for the ladder_planner app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ladder_planner"
    verbose_name = "Ladder Planner"
