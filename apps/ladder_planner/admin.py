"""Django admin registration for the ladder planner."""

from django.contrib import admin

from apps.ladder_planner.models import CachedClub, CachedRider, LadderMatchup, LadderRider


class LadderRiderInline(admin.TabularInline):
    """Inline editor for matchup riders."""

    model = LadderRider
    extra = 0
    fields = ("side", "order", "zwid", "name", "is_racing", "fetched_at")
    readonly_fields = ("fetched_at",)


@admin.register(LadderMatchup)
class LadderMatchupAdmin(admin.ModelAdmin):
    """Admin for ladder matchups."""

    list_display = ("__str__", "our_team_name", "opponent_team_name", "course_profile", "created_by", "updated_at")
    list_filter = ("course_profile",)
    search_fields = ("name", "our_team_name", "opponent_team_name", "course_name")
    inlines = (LadderRiderInline,)


@admin.register(CachedRider)
class CachedRiderAdmin(admin.ModelAdmin):
    """Admin for cached opponent riders."""

    list_display = ("name", "zwid", "club_name", "club_id", "source", "fetched_at")
    list_filter = ("source",)
    search_fields = ("name", "zwid", "club_name")
    readonly_fields = ("fetched_at",)


@admin.register(CachedClub)
class CachedClubAdmin(admin.ModelAdmin):
    """Admin for tracked clubs driving the background refresh."""

    list_display = ("name", "club_id", "rider_count", "auto_refresh", "last_refreshed_at", "last_error")
    list_filter = ("auto_refresh",)
    search_fields = ("name", "club_id")
