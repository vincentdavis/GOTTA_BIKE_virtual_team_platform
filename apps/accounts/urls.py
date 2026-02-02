"""URL patterns for accounts app."""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("profile/", views.profile_view, name="profile"),
    path("profile/<int:user_id>/", views.public_profile_view, name="public_profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("profile/import/<uuid:application_id>/", views.import_application_view, name="import_application"),
    path("profile/delete/", views.profile_delete_confirm, name="profile_delete_confirm"),
    path("profile/delete/confirm/", views.profile_delete, name="profile_delete"),
    path("profile/verify-zwift/", views.verify_zwift, name="verify_zwift"),
    path("profile/unverify-zwift/", views.unverify_zwift, name="unverify_zwift"),
    path("profile/race-ready/", views.submit_race_ready, name="submit_race_ready"),
    path("verification/", views.verification_view, name="verification"),
]
