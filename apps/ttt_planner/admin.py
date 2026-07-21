"""Django admin registration for the TTT planner."""

from django.contrib import admin

from apps.ttt_planner.models import PlanRider, PowerUp, TttPlan


class PlanRiderInline(admin.TabularInline):
    """Inline editor for plan riders."""

    model = PlanRider
    extra = 0


@admin.register(PowerUp)
class PowerUpAdmin(admin.ModelAdmin):
    """Admin for Zwift PowerUps reference data."""

    list_display = ("name", "aka", "duration_seconds", "event_only", "excluded_from_ladder", "order", "is_active")
    list_filter = ("event_only", "excluded_from_ladder", "is_active")
    search_fields = ("name", "aka")
    prepopulated_fields = {"slug": ("name",)}  # noqa: RUF012


@admin.register(TttPlan)
class TttPlanAdmin(admin.ModelAdmin):
    """Admin for TTT plans."""

    list_display = ("__str__", "team_name", "route", "target_speed_kph", "created_by", "updated_at")
    search_fields = ("name", "team_name")
    inlines = (PlanRiderInline,)
