"""Django admin configuration for Zwift Racing models."""

from typing import Any, ClassVar

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import path, reverse

from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.tasks import sync_zr_riders


@admin.register(ZRRider)
class ZRRiderAdmin(admin.ModelAdmin):
    """Admin configuration for ZRRider model."""

    change_list_template = "admin/zwiftracing/zrrider/change_list.html"

    list_display: ClassVar[list[str]] = [
        "name",
        "zwid",
        "country",
        "zp_category",
        "race_current_category",
        "race_current_rating",
        "phenotype_value",
        "club_name",
        "date_left",
    ]
    list_filter: ClassVar[list[str]] = [
        "zp_category",
        "race_current_category",
        "gender",
        "country",
        "phenotype_value",
        "date_left",
    ]
    search_fields: ClassVar[list[str]] = ["name", "zwid", "club_name"]
    ordering: ClassVar[list[str]] = ["name"]
    readonly_fields: ClassVar[list[str]] = ["date_created", "date_modified"]

    fieldsets: ClassVar[list[tuple[str | None, dict[str, Any]]]] = [
        (None, {"fields": ["zwid", "name", "gender", "country", "age", "height", "weight"]}),
        ("ZwiftPower", {"fields": ["zp_category", "zp_ftp"]}),
        (
            "Power (w/kg)",
            {
                "fields": [
                    "power_wkg5",
                    "power_wkg15",
                    "power_wkg30",
                    "power_wkg60",
                    "power_wkg120",
                    "power_wkg300",
                    "power_wkg1200",
                ]
            },
        ),
        (
            "Power (watts)",
            {
                "fields": [
                    "power_w5",
                    "power_w15",
                    "power_w30",
                    "power_w60",
                    "power_w120",
                    "power_w300",
                    "power_w1200",
                ]
            },
        ),
        ("Power Metrics", {"fields": ["power_cp", "power_awc", "power_compound_score"]}),
        (
            "Race Rating - Current",
            {
                "fields": [
                    "race_current_rating",
                    "race_current_date",
                    "race_current_category",
                    "race_current_category_num",
                ]
            },
        ),
        (
            "Race Rating - Last",
            {"fields": ["race_last_rating", "race_last_date", "race_last_category", "race_last_category_num"]},
        ),
        (
            "Race Rating - Max 30 Day",
            {
                "fields": [
                    "race_max30_rating",
                    "race_max30_date",
                    "race_max30_expires",
                    "race_max30_category",
                    "race_max30_category_num",
                ]
            },
        ),
        (
            "Race Rating - Max 90 Day",
            {
                "fields": [
                    "race_max90_rating",
                    "race_max90_date",
                    "race_max90_expires",
                    "race_max90_category",
                    "race_max90_category_num",
                ]
            },
        ),
        ("Race Stats", {"fields": ["race_finishes", "race_dnfs", "race_wins", "race_podiums"]}),
        ("Handicaps", {"fields": ["handicap_flat", "handicap_rolling", "handicap_hilly", "handicap_mountainous"]}),
        (
            "Phenotype",
            {
                "fields": [
                    "phenotype_value",
                    "phenotype_bias",
                    "phenotype_sprinter",
                    "phenotype_puncheur",
                    "phenotype_pursuiter",
                    "phenotype_climber",
                    "phenotype_tt",
                ]
            },
        ),
        ("Club", {"fields": ["club_id", "club_name"]}),
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
                self.admin_site.admin_view(self.sync_from_zwiftracing),
                name="zwiftracing_zrrider_sync",
            ),
        ]
        return custom_urls + urls

    def sync_from_zwiftracing(self, request: HttpRequest) -> HttpResponseRedirect:
        """Handle the sync button click.

        Returns:
            Redirect to the changelist page.

        """
        sync_zr_riders.enqueue()
        self.message_user(
            request,
            "Sync from Zwift Racing task has been queued.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:zwiftracing_zrrider_changelist"))
