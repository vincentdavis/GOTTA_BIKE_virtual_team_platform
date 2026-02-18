"""URL configuration for events app."""

from django.urls import path

from apps.events.views import (
    event_create_view,
    event_delete_view,
    event_detail_view,
    event_edit_view,
    event_list_view,
    event_signup_delete_view,
    event_signup_edit_view,
    event_signup_view,
    event_signup_withdraw_view,
    squad_create_view,
    squad_delete_view,
    squad_edit_view,
)

app_name = "events"

urlpatterns = [
    path("", event_list_view, name="event_list"),
    path("create/", event_create_view, name="event_create"),
    path("<int:pk>/", event_detail_view, name="event_detail"),
    path("<int:pk>/edit/", event_edit_view, name="event_edit"),
    path("<int:pk>/delete/", event_delete_view, name="event_delete"),
    path("<int:pk>/signup/", event_signup_view, name="event_signup"),
    path("<int:pk>/signup/edit/", event_signup_edit_view, name="event_signup_edit"),
    path("<int:pk>/signup/delete/", event_signup_delete_view, name="event_signup_delete"),
    path("<int:event_pk>/signups/<int:signup_pk>/withdraw/", event_signup_withdraw_view, name="event_signup_withdraw"),
    path("<int:event_pk>/squads/add/", squad_create_view, name="squad_create"),
    path("<int:event_pk>/squads/<int:squad_pk>/edit/", squad_edit_view, name="squad_edit"),
    path("<int:event_pk>/squads/<int:squad_pk>/delete/", squad_delete_view, name="squad_delete"),
]
