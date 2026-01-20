"""URL patterns for team app."""

from django.urls import path

from apps.team import views

app_name = "team"

urlpatterns = [
    path("roster/", views.team_roster_view, name="roster"),
    path("roster/f/<uuid:filter_id>/", views.filtered_roster_view, name="filtered_roster"),
    path("links/", views.team_links_view, name="links"),
    path("links/submit/", views.submit_team_link_view, name="submit_link"),
    path("links/<int:pk>/edit/", views.edit_team_link_view, name="edit_link"),
    path("links/<int:pk>/delete/", views.delete_team_link_view, name="delete_link"),
    path("verification/", views.verification_records_view, name="verification_records"),
    path("verification/<int:pk>/", views.verification_record_detail_view, name="verification_record_detail"),
    path("verification/delete-expired-media/", views.delete_expired_media_view, name="delete_expired_media"),
    path("verification/delete-rejected-media/", views.delete_rejected_media_view, name="delete_rejected_media"),
    path("performance-review/", views.performance_review_view, name="performance_review"),
    path("youtube/", views.youtube_channels_view, name="youtube_channels"),
    # Membership Applications
    path("applications/", views.membership_application_list_view, name="application_list"),
    path("applications/<uuid:pk>/", views.membership_application_admin_view, name="application_admin"),
    path("apply/<uuid:pk>/", views.membership_application_public_view, name="application_public"),
    path("apply/<uuid:pk>/verify-zwift/", views.application_verify_zwift, name="application_verify_zwift"),
    path("apply/<uuid:pk>/unverify-zwift/", views.application_unverify_zwift, name="application_unverify_zwift"),
]
