"""App configuration for the TTT planner."""

from django.apps import AppConfig


class TttPlannerConfig(AppConfig):
    """Configuration for the ttt_planner app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ttt_planner"
    verbose_name = "TTT Planner"
