"""URL configuration for the shared Routes pages (mounted at /routes/).

The reference data (worlds/routes/segments) is the canonical Zwift Speed Lab dataset
(:mod:`apps.zwift_data`); routes are keyed by their stable ``name_hash`` and segments by
their ``segment_id`` so links survive a data re-sync. Power-ups remain locally curated,
and the old ``ttt_planner.Route`` / ``Segment`` CRUD stays for planner curation (vELO2
weights, recommended laps — data not present in the canonical dataset).
"""

from django.urls import path

from apps.ttt_planner import views

app_name = "routes"

urlpatterns = [
    path("", views.route_list, name="list"),
    path("check-updates/", views.route_check_updates, name="check_updates"),
    path("load-velo/", views.route_load_velo, name="load_velo"),
    # chart data, fetched lazily when a route detail is opened
    path("api/<int:world_id>/<str:name_hash>/profile/", views.route_profile_json, name="profile_json"),
    path("api/<int:world_id>/<str:name_hash>/segments/", views.route_segments_json, name="route_segments_json"),
    # canonical detail pages (stable keys, survive a re-sync)
    path("r/<str:name_hash>/", views.route_detail, name="detail"),
    path("segments/s/<str:segment_id>/", views.segment_detail, name="segment_detail"),
    # locally-curated planner data (old Route/Segment + PowerUps)
    path("new/", views.route_create, name="create"),
    path("<int:route_id>/edit/", views.route_edit, name="edit"),
    path("segments/new/", views.segment_create, name="segment_create"),
    path("segments/<int:segment_id>/edit/", views.segment_edit, name="segment_edit"),
    path("powerups/new/", views.powerup_create, name="powerup_create"),
    path("powerups/<int:powerup_id>/edit/", views.powerup_edit, name="powerup_edit"),
]
