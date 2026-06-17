"""URL configuration for the shared Routes pages (mounted at /routes/).

Routes are owned by the TTT planner app but are shared reference data (used by the
ladder planner too), so they get their own top-level ``routes`` namespace.
"""

from django.urls import path

from apps.ttt_planner import views

app_name = "routes"

urlpatterns = [
    path("", views.route_list, name="list"),
    path("<int:route_id>/", views.route_detail, name="detail"),
    path("<int:route_id>/gpx/upload/", views.route_gpx_upload, name="gpx_upload"),
    path("<int:route_id>/gpx/<int:gpx_id>/delete/", views.route_gpx_delete, name="gpx_delete"),
]
