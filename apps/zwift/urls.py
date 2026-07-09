"""URL patterns for zwift app (mounted at /user/zauth/)."""

from django.urls import path

from apps.zwift import views

app_name = "zwift"

urlpatterns = [
    path("", views.zauth_view, name="zauth"),
    path("connect/", views.zauth_connect, name="zauth_connect"),
    path("disconnect/", views.zauth_disconnect, name="zauth_disconnect"),
]
