"""URL configuration for CMS app."""

from django.urls import path

from apps.cms import views

app_name = "cms"

urlpatterns = [
    # Admin pages (must come before slug pattern)
    path("manage/", views.page_list, name="page_list"),
    path("manage/create/", views.page_create, name="page_create"),
    path("manage/<int:pk>/edit/", views.page_edit, name="page_edit"),
    path("manage/<int:pk>/delete/", views.page_delete, name="page_delete"),
    # Public page view (slug pattern last)
    path("<slug:slug>/", views.page_detail, name="page_detail"),
]
