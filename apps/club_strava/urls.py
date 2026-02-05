"""URL configuration for club_strava app."""

from django.urls import path

from apps.club_strava import views

app_name = "club_strava"

urlpatterns = [
    path("", views.activity_list_view, name="activity_list"),
    path("sync/", views.sync_activities_view, name="sync_activities"),
]
