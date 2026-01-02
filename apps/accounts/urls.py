"""URL patterns for accounts app."""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("profile/delete/", views.profile_delete_confirm, name="profile_delete_confirm"),
    path("profile/delete/confirm/", views.profile_delete, name="profile_delete"),
    path("profile/verify-zwift/", views.verify_zwift, name="verify_zwift"),
    path("profile/unverify-zwift/", views.unverify_zwift, name="unverify_zwift"),
    path("profile/race-ready/", views.submit_race_ready, name="submit_race_ready"),
]
