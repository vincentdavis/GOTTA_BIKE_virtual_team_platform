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
]
