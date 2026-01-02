"""Admin configuration for data_connection app."""

from typing import ClassVar

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from apps.data_connection import gs_client
from apps.data_connection.models import DataConnection


@admin.register(DataConnection)
class DataConnectionAdmin(admin.ModelAdmin):
    """Admin configuration for DataConnection model."""

    list_display: ClassVar = ["title", "data_sheet", "created_by", "date_created", "date_expires", "is_expired"]
    list_filter: ClassVar = ["created_by", "date_created", "date_expires"]
    search_fields: ClassVar = ["title", "description"]
    readonly_fields: ClassVar = ["date_created", "date_updated", "date_last_synced"]
    change_list_template = "admin/data_connection/dataconnection/change_list.html"

    def get_urls(self):
        """Add custom admin URLs.

        Returns:
            List of URL patterns including custom Drive quota views.

        """
        urls = super().get_urls()
        custom_urls = [
            path(
                "drive-quota/",
                self.admin_site.admin_view(self.drive_quota_view),
                name="data_connection_drive_quota",
            ),
            path(
                "empty-trash/",
                self.admin_site.admin_view(self.empty_trash_view),
                name="data_connection_empty_trash",
            ),
            path(
                "delete-file/<str:file_id>/",
                self.admin_site.admin_view(self.delete_file_view),
                name="data_connection_delete_file",
            ),
        ]
        return custom_urls + urls

    def drive_quota_view(self, request: HttpRequest) -> HttpResponse:
        """Display Drive quota and file list.

        Args:
            request: The HTTP request.

        Returns:
            Template response with quota information.

        """
        context = {
            **self.admin_site.each_context(request),
            "title": "Google Drive Quota",
            "opts": self.model._meta,
        }

        try:
            quota_info = gs_client.get_drive_quota_info()
            context["quota_info"] = quota_info
        except gs_client.GSClientError as e:
            messages.error(request, f"Failed to get Drive quota: {e}")
            context["error"] = str(e)

        return TemplateResponse(request, "admin/data_connection/drive_quota.html", context)

    def empty_trash_view(self, request: HttpRequest) -> HttpResponse:
        """Empty the service account's Drive trash.

        Args:
            request: The HTTP request.

        Returns:
            Redirect to Drive quota page.

        """
        try:
            count = gs_client.empty_trash()
            if count > 0:
                messages.success(request, f"Emptied trash: {count} files permanently deleted.")
            else:
                messages.info(request, "Trash was already empty.")
        except gs_client.GSClientError as e:
            messages.error(request, f"Failed to empty trash: {e}")

        return redirect("admin:data_connection_drive_quota")

    def delete_file_view(self, request: HttpRequest, file_id: str) -> HttpResponse:
        """Permanently delete a file.

        Args:
            request: The HTTP request.
            file_id: The Google Drive file ID to delete.

        Returns:
            Redirect to Drive quota page.

        """
        try:
            gs_client.delete_file(file_id)
            messages.success(request, f"File {file_id} permanently deleted.")
        except gs_client.GSClientError as e:
            messages.error(request, f"Failed to delete file: {e}")

        return redirect("admin:data_connection_drive_quota")
