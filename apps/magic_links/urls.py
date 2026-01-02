"""URL patterns for magic links."""

from django.urls import path

from apps.magic_links import views

app_name = "magic_links"

urlpatterns = [
    path("<str:token>/", views.validate_magic_link, name="validate"),
]
