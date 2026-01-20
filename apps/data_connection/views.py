"""Views for data_connection app."""

import contextlib

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import discord_permission_required
from apps.accounts.models import Permissions
from apps.data_connection import gs_client
from apps.data_connection.forms import DataConnectionFilterForm, DataConnectionForm
from apps.data_connection.gs_client import GSClientError, GSSpreadsheetNotFoundError
from apps.data_connection.models import DataConnection


@login_required
@discord_permission_required(Permissions.DATA_CONNECTION)
def connection_list(request: HttpRequest) -> HttpResponse:
    """List all data connections for the current user.

    Args:
        request: The HTTP request.

    Returns:
        Rendered list page.

    """
    form = DataConnectionFilterForm(request.GET)
    connections = DataConnection.objects.all()

    if form.is_valid():
        # Search filter
        search = form.cleaned_data.get("search")
        if search:
            connections = connections.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        # Expired filter
        show_expired = form.cleaned_data.get("show_expired")
        if not show_expired:
            connections = connections.filter(date_expires__gte=timezone.now().date())

        # Sorting
        sort_by = form.cleaned_data.get("sort_by")
        if sort_by:
            connections = connections.order_by(sort_by)

    # Check for new_sheet parameter (shows popup after creating new sheet)
    new_sheet_connection = None
    new_sheet_id = request.GET.get("new_sheet")
    if new_sheet_id:
        with contextlib.suppress(ValueError, DataConnection.DoesNotExist):
            new_sheet_connection = DataConnection.objects.get(pk=int(new_sheet_id), created_by=request.user)

    return render(request, "data_connection/connection_list.html", {
        "connections": connections,
        "filter_form": form,
        "new_sheet_connection": new_sheet_connection,
    })


@login_required
@discord_permission_required(Permissions.DATA_CONNECTION)
def connection_create(request: HttpRequest) -> HttpResponse:
    """Create a new data connection.

    Args:
        request: The HTTP request.

    Returns:
        Rendered form or redirect to list.

    """
    if request.method == "POST":
        form = DataConnectionForm(request.POST)
        if form.is_valid():
            connection = form.save(commit=False)
            connection.created_by = request.user

            # Handle new sheet creation
            if form.cleaned_data.get("create_new_sheet"):
                try:
                    # Create the spreadsheet in the shared folder
                    spreadsheet_url = gs_client.create_spreadsheet(
                        title=connection.title,
                        sheet_name=connection.data_sheet,
                    )
                    connection.spreadsheet_url = spreadsheet_url

                    # Set headers based on selected fields
                    headers = list(DataConnection.BASE_FIELDS) + connection.selected_fields
                    # Convert field keys to display names for headers
                    field_display_map = dict(DataConnection.USER_FIELDS)
                    field_display_map.update(dict(DataConnection.ZWIFTPOWER_FIELDS))
                    field_display_map.update(dict(DataConnection.ZWIFTRACING_FIELDS))
                    header_names = [field_display_map.get(h, h) for h in headers]
                    gs_client.set_headers(spreadsheet_url, connection.data_sheet, header_names)

                except GSClientError as e:
                    logfire.error(f"Failed to create Google Sheet: {e}")
                    messages.error(request, f"Failed to create Google Sheet: {e}")
                    return render(request, "data_connection/connection_form.html", {
                        "form": form,
                        "action": "Create",
                    })

                # Fetch and store the sheet owner
                owner_email = gs_client.get_spreadsheet_owner(connection.spreadsheet_url)
                if owner_email:
                    connection.owner_email = owner_email

                connection.save()
                # Redirect with new_sheet param to trigger the popup modal
                return redirect(f"{request.build_absolute_uri('/data-connections/')}?new_sheet={connection.pk}")
            else:
                messages.success(request, f"Data connection '{connection.title}' created successfully.")

            # Fetch and store the sheet owner
            owner_email = gs_client.get_spreadsheet_owner(connection.spreadsheet_url)
            if owner_email:
                connection.owner_email = owner_email

            connection.save()
            return redirect("data_connection:connection_list")
    else:
        form = DataConnectionForm()

    return render(request, "data_connection/connection_form.html", {
        "form": form,
        "action": "Create",
    })


@login_required
@discord_permission_required(Permissions.DATA_CONNECTION)
def connection_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit an existing data connection.

    Args:
        request: The HTTP request.
        pk: The primary key of the connection.

    Returns:
        Rendered form or redirect to list.

    """
    connection = get_object_or_404(DataConnection, pk=pk, created_by=request.user)

    if request.method == "POST":
        old_url = connection.spreadsheet_url
        form = DataConnectionForm(request.POST, instance=connection)
        if form.is_valid():
            updated_connection = form.save(commit=False)

            # Refresh owner email if URL changed
            if updated_connection.spreadsheet_url != old_url:
                owner_email = gs_client.get_spreadsheet_owner(updated_connection.spreadsheet_url)
                updated_connection.owner_email = owner_email or ""

            updated_connection.save()
            messages.success(request, f"Data connection '{connection.title}' updated successfully.")
            return redirect("data_connection:connection_list")
    else:
        form = DataConnectionForm(instance=connection)

    return render(request, "data_connection/connection_form.html", {
        "form": form,
        "connection": connection,
        "action": "Edit",
    })


@login_required
@discord_permission_required(Permissions.DATA_CONNECTION)
def connection_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a data connection.

    Args:
        request: The HTTP request.
        pk: The primary key of the connection.

    Returns:
        Redirect to list.

    """
    connection = get_object_or_404(DataConnection, pk=pk, created_by=request.user)

    if request.method == "POST":
        title = connection.title
        connection.delete()
        messages.success(request, f"Data connection '{title}' deleted.")
        return redirect("data_connection:connection_list")

    return render(request, "data_connection/connection_confirm_delete.html", {
        "connection": connection,
    })


@login_required
@discord_permission_required(Permissions.DATA_CONNECTION)
def connection_sync(request: HttpRequest, pk: int) -> HttpResponse:
    """Sync data connection to Google Sheets.

    Args:
        request: The HTTP request.
        pk: The primary key of the connection.

    Returns:
        Redirect to list with success/error message.

    """
    from apps.data_connection.services import sync_connection

    connection = get_object_or_404(DataConnection, pk=pk)

    if connection.is_expired:
        messages.error(request, f"Cannot sync '{connection.title}' - connection has expired.")
        return redirect("data_connection:connection_list")

    if connection.is_broken:
        messages.error(request, f"Cannot sync '{connection.title}' - spreadsheet is broken/deleted.")
        return redirect("data_connection:connection_list")

    try:
        row_count = sync_connection(connection)
        # Clear broken flag on successful sync
        if connection.is_broken:
            connection.is_broken = False
            connection.save(update_fields=["is_broken"])
        messages.success(request, f"Synced {row_count} rows to '{connection.title}'.")
    except GSSpreadsheetNotFoundError:
        # Mark connection as broken when spreadsheet is not found
        connection.is_broken = True
        connection.save(update_fields=["is_broken"])
        logfire.error(f"Spreadsheet not found for connection: {connection.title}", connection_id=pk)
        messages.error(request, f"Spreadsheet for '{connection.title}' was deleted or is inaccessible.")
    except GSClientError as e:
        logfire.error(f"Failed to sync connection: {e}", connection_id=pk)
        messages.error(request, f"Failed to sync: {e}")

    return redirect("data_connection:connection_list")
