"""Django admin registration for the TTT planner."""

from django.contrib import admin

from apps.ttt_planner.models import PlanRider, Route, RouteGpx, TttPlan


class RouteGpxInline(admin.TabularInline):
    """Inline editor for a route's GPX files."""

    model = RouteGpx
    extra = 0
    fields = ("label", "file", "distance_km", "elevation_m", "terrain", "uploaded_by", "uploaded_at")
    readonly_fields = ("distance_km", "elevation_m", "terrain", "uploaded_by", "uploaded_at")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    """Admin for TTT routes."""

    list_display = ("name", "world", "distance_km", "elevation_m", "is_active")
    list_filter = ("is_active", "world")
    search_fields = ("name", "world")
    inlines = (RouteGpxInline,)


class PlanRiderInline(admin.TabularInline):
    """Inline editor for plan riders."""

    model = PlanRider
    extra = 0


@admin.register(TttPlan)
class TttPlanAdmin(admin.ModelAdmin):
    """Admin for TTT plans."""

    list_display = ("__str__", "team_name", "route", "target_speed_kph", "created_by", "updated_at")
    search_fields = ("name", "team_name")
    inlines = (PlanRiderInline,)
