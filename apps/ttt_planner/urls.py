"""URL configuration for the TTT planner app."""

from django.urls import path

from apps.ttt_planner import views

app_name = "ttt_planner"

urlpatterns = [
    path("", views.planner_list, name="list"),
    path("new/", views.plan_create, name="create"),
    path("<uuid:plan_id>/", views.planner_detail, name="detail"),
    path("<uuid:plan_id>/delete/", views.plan_delete, name="delete"),
    path("<uuid:plan_id>/update/", views.plan_update, name="update"),
    path("<uuid:plan_id>/calculate/", views.calculate_speed, name="calculate_speed"),
    path("<uuid:plan_id>/auto-balance/", views.auto_balance, name="auto_balance"),
    path("<uuid:plan_id>/zwiftgopher/", views.zwiftgopher_panel, name="zwiftgopher_panel"),
    path("<uuid:plan_id>/zwiftgopher/run/", views.zwiftgopher_run, name="zwiftgopher_run"),
    path("<uuid:plan_id>/draft-savings/", views.draft_savings_update, name="draft_savings_update"),
    path("<uuid:plan_id>/riders/search/", views.rider_search, name="rider_search"),
    path("<uuid:plan_id>/riders/add/<int:zwid>/", views.rider_add, name="rider_add"),
    path("<uuid:plan_id>/riders/add-squad/", views.plan_squad_add, name="plan_squad_add"),
    path("<uuid:plan_id>/riders/add-manual/", views.rider_add_manual, name="rider_add_manual"),
    path("<uuid:plan_id>/riders/remove-selected/", views.riders_remove_selected, name="riders_remove_selected"),
    path("<uuid:plan_id>/riders/<int:rider_id>/remove/", views.rider_remove, name="rider_remove"),
    path("<uuid:plan_id>/riders/<int:rider_id>/move/<str:direction>/", views.rider_reorder, name="rider_reorder"),
    path("<uuid:plan_id>/riders/<int:rider_id>/update/", views.rider_update, name="rider_update"),
]
