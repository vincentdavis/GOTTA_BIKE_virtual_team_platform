"""URL patterns for the tickets app."""

from django.urls import path

from apps.tickets.views import (
    ticket_create_view,
    ticket_detail_view,
    ticket_edit_view,
    ticket_list_view,
)

app_name = "tickets"

urlpatterns = [
    path("", ticket_list_view, name="ticket_list"),
    path("new/", ticket_create_view, name="ticket_create"),
    path("<int:pk>/", ticket_detail_view, name="ticket_detail"),
    path("<int:pk>/edit/", ticket_edit_view, name="ticket_edit"),
]
