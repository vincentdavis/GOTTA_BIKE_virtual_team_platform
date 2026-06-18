"""URL configuration for the ladder planner app."""

from django.urls import path

from apps.ladder_planner import views

app_name = "ladder_planner"

urlpatterns = [
    path("", views.matchup_list, name="list"),
    path("new/", views.matchup_create, name="create"),
    path("<uuid:matchup_id>/", views.matchup_detail, name="detail"),
    path("<uuid:matchup_id>/delete/", views.matchup_delete, name="delete"),
    path("<uuid:matchup_id>/update/", views.matchup_update, name="update"),
    path("<uuid:matchup_id>/refresh/", views.matchup_refresh, name="refresh"),
    path("<uuid:matchup_id>/ours/search/", views.our_rider_search, name="our_rider_search"),
    path("<uuid:matchup_id>/ours/add/<int:zwid>/", views.our_rider_add, name="our_rider_add"),
    path("<uuid:matchup_id>/ours/add-squad/", views.our_squad_add, name="our_squad_add"),
    path("<uuid:matchup_id>/opponents/search/", views.opponent_search, name="opponent_search"),
    path("<uuid:matchup_id>/opponents/add/", views.opponents_add, name="opponents_add"),
    path("<uuid:matchup_id>/opponents/add/<int:zwid>/", views.opponent_add, name="opponent_add"),
    path("<uuid:matchup_id>/riders/<int:rider_id>/remove/", views.rider_remove, name="rider_remove"),
    path("<uuid:matchup_id>/riders/<int:rider_id>/toggle/", views.rider_toggle, name="rider_toggle"),
]
