"""Admin for cached rider profiles.

Read-only throughout. Every field is a copy of something the zauth service holds, so an edit
here would be silently overwritten by the next sync -- and a form that appears to save but
does not is worse than no form. Use it to look, and fix data at the source.
"""

import json

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from apps.rider_data.models import RiderProfile


class HasLastRaceFilter(admin.SimpleListFilter):
    """Split rows by whether the retention anchor is populated.

    This is the filter the deletion policy turns on. ``last_race_at`` is derived from
    ZwiftPower results and excludes races with no club, so a rider can be active and still
    have no date -- and rows without one are never evicted. Seeing the split is the quickest
    way to judge whether a retention window would do what it appears to.
    """

    title = "last race known"
    parameter_name = "has_last_race"

    def lookups(self, request, model_admin):
        """Return the filter options.

        Args:
            request: The admin request.
            model_admin: The model admin instance.

        Returns:
            Pairs of query value and label.

        """
        return (("yes", "Yes — evictable"), ("no", "No — never evicted"))

    def queryset(self, request, queryset):
        """Narrow by whether a last race is known.

        Args:
            request: The admin request.
            queryset: The queryset to filter.

        Returns:
            The filtered queryset.

        """
        if self.value() == "yes":
            return queryset.filter(last_race_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(last_race_at__isnull=True)
        return queryset


@admin.register(RiderProfile)
class RiderProfileAdmin(admin.ModelAdmin):
    """Browse the profile cache."""

    list_display = (
        "zwid",
        "name",
        "gender",
        "category_racing",
        "velo",
        "weight_kg",
        "last_race_display",
        "freshness",
    )
    list_filter = (HasLastRaceFilter, "gender", "category_racing", "category_open")
    search_fields = ("name", "zwid", "club_name")
    ordering = ("-last_race_at", "name")
    list_per_page = 50

    fieldsets = (
        ("Identity", {"fields": ("zwid", "zwift_user_id", "name", "gender", "country", "age")}),
        ("Physical and power", {"fields": ("weight_kg", "height_cm", "ftp", "zftp")}),
        ("Category", {"fields": ("category_open", "category_women", "category_racing")}),
        ("Ratings", {"fields": ("velo", "zwift_racing_score", "zp_skill", "compound_score", "phenotype_value")}),
        ("Club", {"fields": ("club_id", "club_name")}),
        (
            "Cache",
            {
                "fields": ("fetched_at", "last_race_at", "freshness", "sources_pretty", "has_account_pretty"),
                "description": (
                    "has_account says whether we hold data from each source. It does NOT mean the rider is "
                    "connected to this app, and nothing may read it that way."
                ),
            },
        ),
        ("Full document", {"classes": ("collapse",), "fields": ("payload_pretty",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        """Everything is read-only; the next sync overwrites any edit.

        Args:
            request: The admin request.
            obj: The instance being viewed.

        Returns:
            Every field name, plus the computed displays.

        """
        return [field.name for field in self.model._meta.fields] + [
            "freshness",
            "sources_pretty",
            "has_account_pretty",
            "payload_pretty",
            "last_race_display",
        ]

    def has_add_permission(self, request):
        """Rows arrive from the sync, never by hand.

        Args:
            request: The admin request.

        Returns:
            False, always.

        """
        return False

    @admin.display(description="Last race", ordering="last_race_at")
    def last_race_display(self, obj: RiderProfile) -> str:
        """Show the retention anchor, saying plainly when it is absent.

        Args:
            obj: The profile.

        Returns:
            The date, or a note that the row cannot be evicted.

        """
        if obj.last_race_at is None:
            return format_html('<span style="color:#888">none — not evictable</span>')
        days = (timezone.now() - obj.last_race_at).days
        return format_html("{} <span style='color:#888'>({} days ago)</span>", obj.last_race_at.date(), days)

    @admin.display(description="Fetched", ordering="fetched_at")
    def freshness(self, obj: RiderProfile) -> str:
        """Show fetch age and whether the row counts as stale.

        Args:
            obj: The profile.

        Returns:
            A short description of cache freshness.

        """
        age = timezone.now() - obj.fetched_at
        hours = int(age.total_seconds() // 3600)
        label = f"{hours}h ago" if hours < 48 else f"{age.days}d ago"
        if obj.is_stale:
            return format_html('{} <span style="color:#b45309">stale</span>', label)
        return label

    @admin.display(description="Sources")
    def sources_pretty(self, obj: RiderProfile) -> str:
        """Render the provenance block readably.

        Args:
            obj: The profile.

        Returns:
            Pre-formatted JSON.

        """
        return format_html("<pre>{}</pre>", json.dumps(obj.sources, indent=2, sort_keys=True))

    @admin.display(description="Has account")
    def has_account_pretty(self, obj: RiderProfile) -> str:
        """Render the per-source presence block readably.

        Args:
            obj: The profile.

        Returns:
            Pre-formatted JSON.

        """
        return format_html("<pre>{}</pre>", json.dumps(obj.has_account, indent=2, sort_keys=True))

    @admin.display(description="ProfileFull payload")
    def payload_pretty(self, obj: RiderProfile) -> str:
        """Render the stored document, which is where everything uncolumned lives.

        Args:
            obj: The profile.

        Returns:
            Pre-formatted JSON.

        """
        return format_html(
            "<pre style='max-height:32rem;overflow:auto'>{}</pre>",
            json.dumps(obj.payload, indent=2, sort_keys=True),
        )
