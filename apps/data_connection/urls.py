"""URL configuration for data_connection app."""

from django.urls import path

from apps.data_connection import views

app_name = "data_connection"

urlpatterns = [
    path("", views.connection_list, name="connection_list"),
    path("create/", views.connection_create, name="connection_create"),
    path("<int:pk>/edit/", views.connection_edit, name="connection_edit"),
    path("<int:pk>/delete/", views.connection_delete, name="connection_delete"),
    path("<int:pk>/sync/", views.connection_sync, name="connection_sync"),
]
