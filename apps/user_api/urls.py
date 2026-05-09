"""URL configuration for the user-facing API management page."""

from django.urls import path

from apps.user_api.views import api_keys_create, api_keys_list, api_keys_revoke

app_name = "user_api"

urlpatterns = [
    path("", api_keys_list, name="api_keys_list"),
    path("create/", api_keys_create, name="api_keys_create"),
    path("<int:key_id>/revoke/", api_keys_revoke, name="api_keys_revoke"),
]
