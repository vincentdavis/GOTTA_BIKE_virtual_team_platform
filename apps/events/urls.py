"""URL configuration for events app."""

from django.urls import path

from apps.events.views import (
    event_create_view,
    event_delete_view,
    event_detail_view,
    event_edit_view,
    event_list_view,
)

app_name = "events"

urlpatterns = [
    path("", event_list_view, name="event_list"),
    path("create/", event_create_view, name="event_create"),
    path("<int:pk>/", event_detail_view, name="event_detail"),
    path("<int:pk>/edit/", event_edit_view, name="event_edit"),
    path("<int:pk>/delete/", event_delete_view, name="event_delete"),
]
