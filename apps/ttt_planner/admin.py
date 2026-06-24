"""Django admin registration for the TTT planner."""

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import path, reverse

from apps.ttt_planner.models import PlanRider, Route, RouteGpx, Segment, TttPlan
from apps.ttt_planner.services.segment_import import import_segments


class RouteGpxInline(admin.TabularInline):
    """Inline editor for a route's GPX files."""

    model = RouteGpx
    extra = 0
    fields = ("label", "file", "distance_km", "elevation_m", "terrain", "uploaded_by", "uploaded_at")
    readonly_fields = ("distance_km", "elevation_m", "terrain", "uploaded_by", "uploaded_at")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    """Admin for TTT routes."""

    list_display = ("name", "world", "distance_km", "elevation_m", "supports_laps", "recommended_laps", "is_active")
    list_filter = ("is_active", "world", "supports_laps")
    search_fields = ("name", "world")
    filter_horizontal = ("segments",)
    inlines = (RouteGpxInline,)


@admin.register(Segment)
class SegmentAdmin(admin.ModelAdmin):
    """Admin for climb / sprint segments, with a bundled-dataset import button."""

    change_list_template = "admin/ttt_planner/segment/change_list.html"
    list_display = ("name", "segment_type", "direction", "world", "category", "length_m", "grade_pct", "elevation_m")
    list_filter = ("segment_type", "direction", "world")
    search_fields = ("name", "world")

    def get_urls(self) -> list:
        """Add the bundled-dataset import URL.

        Returns:
            URL patterns including the custom import URL.

        """
        custom = [
            path(
                "import/",
                self.admin_site.admin_view(self.import_segments_view),
                name="ttt_planner_segment_import",
            ),
        ]
        return custom + super().get_urls()

    def import_segments_view(self, request: HttpRequest) -> HttpResponseRedirect:
        """Import segments from the bundled JSON dataset (idempotent upsert).

        Returns:
            Redirect back to the segment changelist.

        """
        counts = import_segments()
        self.message_user(
            request,
            f"Segments imported: {counts['created']} created, {counts['updated']} updated, "
            f"{counts['skipped']} skipped.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:ttt_planner_segment_changelist"))


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
