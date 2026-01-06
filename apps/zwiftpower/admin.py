"""Django admin configuration for ZwiftPower models."""

from typing import Any, ClassVar

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import path, reverse
from simple_history.admin import SimpleHistoryAdmin

from apps.zwiftpower.models import ZPEvent, ZPRiderResults, ZPTeamRiders
from apps.zwiftpower.tasks import update_team_results, update_team_riders


@admin.register(ZPTeamRiders)
class ZPTeamRidersAdmin(SimpleHistoryAdmin):
    """Admin configuration for ZPTeamRiders model with history tracking."""

    change_list_template = "admin/zwiftpower/zpteamriders/change_list.html"

    list_display: ClassVar[list[str]] = [
        "name",
        "zwid",
        "flag",
        "div",
        "ftp",
        "weight",
        "skill",
        "date_left",
    ]
    list_filter: ClassVar[list[str]] = ["div", "date_left"]
    search_fields: ClassVar[list[str]] = ["name", "zwid", "aid"]
    ordering: ClassVar[list[str]] = ["name"]
    readonly_fields: ClassVar[list[str]] = ["date_created", "date_modified"]
    actions: ClassVar[list[str]] = ["run_update_team_riders"]

    fieldsets: ClassVar[list[tuple[str | None, dict[str, Any]]]] = [
        (None, {"fields": ["zwid", "aid", "name", "flag", "age"]}),
        ("Division", {"fields": ["div", "divw", "r", "rank"]}),
        ("Physical", {"fields": ["ftp", "weight"]}),
        ("Skills", {"fields": ["skill", "skill_race", "skill_seg", "skill_power"]}),
        ("Stats", {"fields": ["distance", "climbed", "energy", "time"]}),
        ("Power Records", {"fields": ["h_1200_watts", "h_1200_wkg", "h_15_watts", "h_15_wkg"]}),
        ("Status", {"fields": ["status", "reg", "email", "zada"]}),
        ("Timestamps", {"fields": ["date_created", "date_modified", "date_left"]}),
    ]

    def get_urls(self) -> list:
        """Add custom URLs for sync action.

        Returns:
            List of URL patterns including custom sync URL.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "sync/",
                self.admin_site.admin_view(self.sync_from_zwiftpower),
                name="zwiftpower_zpteamriders_sync",
            ),
        ]
        return custom_urls + urls

    def sync_from_zwiftpower(self, request: HttpRequest) -> HttpResponseRedirect:
        """Handle the sync button click.

        Returns:
            Redirect to the changelist page.

        """
        update_team_riders.enqueue()
        self.message_user(
            request,
            "Sync from ZwiftPower task has been queued.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:zwiftpower_zpteamriders_changelist"))

    @admin.action(description="Update team riders from ZwiftPower")
    def run_update_team_riders(self, request: HttpRequest, queryset: Any) -> None:
        """Enqueue the update_team_riders background task."""
        update_team_riders.enqueue()
        self.message_user(
            request,
            "Update team riders task has been queued.",
            messages.SUCCESS,
        )


@admin.register(ZPEvent)
class ZPEventAdmin(admin.ModelAdmin):
    """Admin configuration for ZPEvent model."""

    change_list_template = "admin/zwiftpower/zpevent/change_list.html"

    list_display: ClassVar[list[str]] = [
        "zid",
        "title",
        "event_date",
        "results_count",
    ]
    list_filter: ClassVar[list[str]] = ["event_date"]
    search_fields: ClassVar[list[str]] = ["title", "zid"]
    ordering: ClassVar[list[str]] = ["-event_date"]
    readonly_fields: ClassVar[list[str]] = ["date_created", "date_modified"]
    date_hierarchy = "event_date"

    fieldsets: ClassVar[list[tuple[str | None, dict[str, Any]]]] = [
        (None, {"fields": ["zid", "title", "event_date"]}),
        ("Timestamps", {"fields": ["date_created", "date_modified"]}),
    ]

    def results_count(self, obj: ZPEvent) -> int:
        """Return the number of results for this event.

        Args:
            obj: The ZPEvent instance.

        Returns:
            Number of results for this event.

        """
        return obj.results.count()

    results_count.short_description = "Results"  # type: ignore[attr-defined]

    def get_urls(self) -> list:
        """Add custom URLs for sync action.

        Returns:
            List of URL patterns including custom sync URL.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "sync/",
                self.admin_site.admin_view(self.sync_team_results),
                name="zwiftpower_zpevent_sync",
            ),
        ]
        return custom_urls + urls

    def sync_team_results(self, request: HttpRequest) -> HttpResponseRedirect:
        """Handle the sync button click.

        Returns:
            Redirect to the changelist page.

        """
        update_team_results.enqueue()
        self.message_user(
            request,
            "Sync team results from ZwiftPower task has been queued.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:zwiftpower_zpevent_changelist"))


@admin.register(ZPRiderResults)
class ZPRiderResultsAdmin(admin.ModelAdmin):
    """Admin configuration for ZPRiderResults model."""

    change_list_template = "admin/zwiftpower/zpriderresults/change_list.html"

    list_display: ClassVar[list[str]] = [
        "name",
        "event",
        "category",
        "pos",
        "position_in_cat",
        "time_display",
        "avg_wkg",
        "avg_power",
        "weight",
        "height",
    ]
    list_filter: ClassVar[list[str]] = ["category", "event__event_date", "tname"]
    search_fields: ClassVar[list[str]] = ["name", "zwid", "event__title"]
    ordering: ClassVar[list[str]] = ["-event__event_date", "pos"]
    readonly_fields: ClassVar[list[str]] = ["date_created", "date_modified"]
    raw_id_fields: ClassVar[list[str]] = ["event"]
    list_select_related: ClassVar[list[str]] = ["event"]

    fieldsets: ClassVar[list[tuple[str | None, dict[str, Any]]]] = [
        (None, {"fields": ["event", "zid", "zwid", "res_id"]}),
        ("Rider Info", {"fields": ["name", "flag", "age", "male"]}),
        ("Team", {"fields": ["tid", "tname"]}),
        ("Results", {"fields": ["pos", "position_in_cat", "category", "label"]}),
        ("Timing", {"fields": ["time_seconds", "time_gun", "gap"]}),
        ("Physical", {"fields": ["ftp", "weight", "height"]}),
        ("Power", {"fields": ["avg_power", "avg_wkg", "np", "wftp", "wkg_ftp"]}),
        (
            "Power Curve (Watts)",
            {"fields": ["w5", "w15", "w30", "w60", "w120", "w300", "w1200"], "classes": ["collapse"]},
        ),
        (
            "Power Curve (W/kg)",
            {"fields": ["wkg5", "wkg15", "wkg30", "wkg60", "wkg120", "wkg300", "wkg1200"], "classes": ["collapse"]},
        ),
        ("Heart Rate", {"fields": ["avg_hr", "max_hr", "hrm"]}),
        ("Division/Skill", {"fields": ["div", "divw", "skill", "skill_gain"]}),
        ("Status", {"fields": ["zada", "reg", "penalty", "upg", "f_t"]}),
        ("Timestamps", {"fields": ["date_created", "date_modified"]}),
    ]

    def time_display(self, obj: ZPRiderResults) -> str:
        """Format time as mm:ss.

        Returns:
            Formatted time string.

        """
        if obj.time_seconds is None:
            return "-"
        total_seconds = int(obj.time_seconds)
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"

    time_display.short_description = "Time"  # type: ignore[attr-defined]

    def get_urls(self) -> list:
        """Add custom URLs for sync action.

        Returns:
            List of URL patterns including custom sync URL.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "sync/",
                self.admin_site.admin_view(self.sync_team_results),
                name="zwiftpower_zpriderresults_sync",
            ),
        ]
        return custom_urls + urls

    def sync_team_results(self, request: HttpRequest) -> HttpResponseRedirect:
        """Handle the sync button click.

        Returns:
            Redirect to the changelist page.

        """
        update_team_results.enqueue()
        self.message_user(
            request,
            "Sync team results from ZwiftPower task has been queued.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:zwiftpower_zpriderresults_changelist"))
