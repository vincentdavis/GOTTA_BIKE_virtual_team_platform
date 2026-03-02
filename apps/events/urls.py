"""URL configuration for events app."""

from django.urls import path

from apps.events.views import (
    availability_create_view,
    availability_respond_view,
    availability_results_view,
    availability_status_view,
    event_create_view,
    event_delete_view,
    event_detail_view,
    event_edit_view,
    event_list_view,
    event_signup_delete_view,
    event_signup_edit_view,
    event_signup_view,
    event_signup_withdraw_view,
    event_toggle_role_view,
    manage_roles_view,
    my_events_view,
    squad_assign_view,
    squad_create_view,
    squad_delete_view,
    squad_edit_view,
    squad_invite_view,
    squad_manage_view,
    squad_regenerate_token_view,
    squad_toggle_role_view,
)

app_name = "events"

urlpatterns = [
    path("squad-invite/<uuid:token>/", squad_invite_view, name="squad_invite"),
    path("", event_list_view, name="event_list"),
    path("my-events/", my_events_view, name="my_events"),
    path("create/", event_create_view, name="event_create"),
    path("<int:pk>/", event_detail_view, name="event_detail"),
    path("<int:pk>/edit/", event_edit_view, name="event_edit"),
    path("<int:pk>/delete/", event_delete_view, name="event_delete"),
    path("<int:pk>/signup/", event_signup_view, name="event_signup"),
    path("<int:pk>/signup/edit/", event_signup_edit_view, name="event_signup_edit"),
    path("<int:pk>/signup/delete/", event_signup_delete_view, name="event_signup_delete"),
    path("<int:event_pk>/signups/<int:signup_pk>/withdraw/", event_signup_withdraw_view, name="event_signup_withdraw"),
    path("<int:event_pk>/manage-roles/", manage_roles_view, name="manage_roles"),
    path("<int:event_pk>/toggle-event-role/<int:user_id>/", event_toggle_role_view, name="event_toggle_role"),
    path("<int:event_pk>/squads/manage/", squad_manage_view, name="squad_manage"),
    path("<int:event_pk>/squads/assign/", squad_assign_view, name="squad_assign"),
    path("<int:event_pk>/squads/add/", squad_create_view, name="squad_create"),
    path("<int:event_pk>/squads/<int:squad_pk>/edit/", squad_edit_view, name="squad_edit"),
    path("<int:event_pk>/squads/<int:squad_pk>/delete/", squad_delete_view, name="squad_delete"),
    path(
        "<int:event_pk>/squads/<int:squad_pk>/toggle-role/<int:user_id>/",
        squad_toggle_role_view,
        name="squad_toggle_role",
    ),
    path(
        "<int:event_pk>/squads/<int:squad_pk>/regenerate-token/",
        squad_regenerate_token_view,
        name="squad_regenerate_token",
    ),
    path(
        "<int:event_pk>/squads/<int:squad_pk>/availability/create/",
        availability_create_view,
        name="availability_create",
    ),
    path(
        "<int:event_pk>/squads/<int:squad_pk>/availability/<uuid:grid_pk>/status/",
        availability_status_view,
        name="availability_status",
    ),
    path(
        "<int:event_pk>/squads/<int:squad_pk>/availability/<uuid:grid_pk>/",
        availability_respond_view,
        name="availability_respond",
    ),
    path(
        "<int:event_pk>/squads/<int:squad_pk>/availability/<uuid:grid_pk>/results/",
        availability_results_view,
        name="availability_results",
    ),
]
