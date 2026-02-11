"""Views for events app."""

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import team_member_required
from apps.events.forms import EventForm
from apps.events.models import Event


@require_GET
@login_required
@team_member_required()
def event_list_view(request: HttpRequest) -> HttpResponse:
    """Display list of visible events.

    Args:
        request: The HTTP request.

    Returns:
        Rendered event list page.

    """
    events = Event.objects.filter(visible=True)
    search_query = request.GET.get("q", "").strip()
    if search_query:
        events = events.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))
    logfire.debug("Event list viewed", user_id=request.user.id, event_count=events.count())
    return render(
        request,
        "events/event_list.html",
        {
            "events": events,
            "search_query": search_query,
            "is_event_admin": request.user.is_event_admin,
        },
    )


@require_GET
@login_required
@team_member_required()
def event_detail_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Display event detail with related races.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Rendered event detail page.

    """
    event = get_object_or_404(Event, pk=pk)
    races = event.races.all()
    logfire.debug("Event detail viewed", user_id=request.user.id, event_id=pk)
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "races": races,
            "is_event_admin": request.user.is_event_admin,
        },
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def event_create_view(request: HttpRequest) -> HttpResponse:
    """Create a new event.

    Args:
        request: The HTTP request.

    Returns:
        Rendered form or redirect on success.

    """
    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized event creation attempt",
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to create events.")
        return redirect("events:event_list")

    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.created_by = request.user
            event.save()
            logfire.info(
                "Event created",
                event_id=event.pk,
                event_title=event.title,
                user_id=request.user.id,
                username=request.user.username,
            )
            messages.success(request, "Event created successfully!")
            return redirect("events:event_detail", pk=event.pk)
    else:
        form = EventForm()

    return render(
        request,
        "events/event_form.html",
        {"form": form, "page_title": "Create Event", "submit_label": "Create Event"},
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def event_edit_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit an existing event.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Rendered form or redirect on success.

    """
    event = get_object_or_404(Event, pk=pk)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized event edit attempt",
            event_id=pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to edit events.")
        return redirect("events:event_detail", pk=pk)

    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            logfire.info(
                "Event updated",
                event_id=pk,
                event_title=event.title,
                user_id=request.user.id,
                username=request.user.username,
            )
            messages.success(request, "Event updated successfully!")
            return redirect("events:event_detail", pk=pk)
    else:
        form = EventForm(instance=event)

    return render(
        request,
        "events/event_form.html",
        {"form": form, "event": event, "page_title": "Edit Event", "submit_label": "Save Changes"},
    )


@login_required
@team_member_required()
@require_POST
def event_delete_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete an event.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Redirect to event list.

    """
    event = get_object_or_404(Event, pk=pk)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized event delete attempt",
            event_id=pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to delete events.")
        return redirect("events:event_list")

    event_title = event.title
    event.delete()
    logfire.info(
        "Event deleted",
        event_id=pk,
        event_title=event_title,
        user_id=request.user.id,
        username=request.user.username,
    )
    messages.success(request, f'Event "{event_title}" deleted successfully!')
    return redirect("events:event_list")
