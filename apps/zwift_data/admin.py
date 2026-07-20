"""Admin for the canonical Zwift dataset (read-mostly reference data)."""

from django.contrib import admin

from .models import ZwiftDataset, ZwiftRoute, ZwiftSegment, ZwiftWorld


@admin.register(ZwiftWorld)
class ZwiftWorldAdmin(admin.ModelAdmin):
    """Zwift worlds with dataset counts."""

    list_display = ("name", "world_id", "route_count", "segment_count")
    ordering = ("name",)
    search_fields = ("name",)


@admin.register(ZwiftRoute)
class ZwiftRouteAdmin(admin.ModelAdmin):
    """Canonical routes (synced; edits are overwritten on the next sync)."""

    list_display = ("name", "world", "sport", "distance_km", "ascent_m", "supports_tt", "event_only")
    list_filter = ("world", "sport", "supports_tt", "event_only")
    search_fields = ("name", "world", "name_hash")
    ordering = ("world", "name")


@admin.register(ZwiftSegment)
class ZwiftSegmentAdmin(admin.ModelAdmin):
    """Canonical live segments (synced; edits are overwritten on the next sync)."""

    list_display = ("display_name", "segment_type", "world", "length_m", "gives_powerup", "route_count")
    list_filter = ("segment_type", "world", "gives_powerup")
    search_fields = ("name", "world")
    ordering = ("world", "name")


@admin.register(ZwiftDataset)
class ZwiftDatasetAdmin(admin.ModelAdmin):
    """The single dataset-version row."""

    list_display = ("__str__", "synced_at", "routes_count", "segments_count", "worlds_count", "syncing", "last_error")
    readonly_fields = (
        "source_url",
        "synced_at",
        "bundle_last_modified",
        "bundle_bytes",
        "routes_count",
        "segments_count",
        "worlds_count",
        "profiles_count",
        "last_error",
        "syncing",
    )
