"""URL configuration for zwiftpower app."""

from django.urls import path

from apps.zwiftpower import views

app_name = "zwiftpower"

urlpatterns = [
    path("results/", views.team_results_view, name="team_results"),
    path("results/<int:zid>/", views.event_results_view, name="event_results"),
]
