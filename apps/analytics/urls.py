"""URL configuration for analytics app."""

from django.urls import path

from apps.analytics import views

app_name = "analytics"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
]
