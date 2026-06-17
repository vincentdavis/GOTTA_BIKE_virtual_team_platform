"""Django admin registration for the ladder planner."""

from django.contrib import admin

from apps.ladder_planner.models import LadderMatchup, LadderRider


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
