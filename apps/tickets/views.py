"""Views for the tickets app."""

from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import team_member_required
from apps.accounts.models import Permissions
from apps.tickets.forms import TicketCreateForm, TicketEditForm
from apps.tickets.models import Ticket

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User


def visible_tickets(user: User):
    """Return the tickets a user may see.

    Holders of ``ticket_admin`` (set in Constance under Permission Mappings) see the
    whole queue. Everyone else sees the tickets they submitted, plus any assigned to
    them -- the assignee picker offers every user, so leaving those out would hide a
    ticket from the person expected to work it.

    Args:
        user: The requesting user.

    Returns:
        A ``Ticket`` queryset filtered to what this user may see.

    """
    qs = Ticket.objects.select_related("submitted_by", "assigned_to")
    if user.has_permission(Permissions.TICKET_ADMIN):
        return qs
    return qs.filter(Q(submitted_by=user) | Q(assigned_to=user))


@login_required
@team_member_required()
def ticket_list_view(request: HttpRequest) -> HttpResponse:
    """List tickets with simple filters (status, category, mine, search).

    Args:
        request: The HTTP request.

    Returns:
        Rendered ticket list page.

    """
    qs = visible_tickets(request.user)

    status = request.GET.get("status", "")
    category = request.GET.get("category", "")
    mine = request.GET.get("mine", "") == "1"
    q = request.GET.get("q", "").strip()

    if status:
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category=category)
    if mine:
        qs = qs.filter(Q(submitted_by=request.user) | Q(assigned_to=request.user))
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(details__icontains=q))

    return render(
        request,
        "tickets/ticket_list.html",
        {
            "tickets": qs,
            "status_choices": Ticket.Status.choices,
            "category_choices": Ticket.Category.choices,
            "filter_status": status,
            "filter_category": category,
            "filter_mine": mine,
            "filter_q": q,
        },
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def ticket_create_view(request: HttpRequest) -> HttpResponse:
    """Create a new ticket for the current user.

    Args:
        request: The HTTP request.

    Returns:
        Rendered create form, or redirect to the detail page on success.

    """
    if request.method == "POST":
        form = TicketCreateForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.submitted_by = request.user
            ticket.save()
            logfire.info(
                "Ticket created",
                ticket_id=ticket.pk,
                user_id=request.user.id,
                category=ticket.category,
                priority=ticket.priority,
            )
            messages.success(request, f"Ticket #{ticket.pk} created.")
            return redirect("tickets:ticket_detail", pk=ticket.pk)
    else:
        form = TicketCreateForm()

    return render(
        request,
        "tickets/ticket_form.html",
        {
            "form": form,
            "page_title": "New Ticket",
            "submit_label": "Submit Ticket",
        },
    )


@login_required
@team_member_required()
def ticket_detail_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Show a single ticket.

    Args:
        request: The HTTP request.
        pk: The ticket primary key.

    Returns:
        Rendered detail page.

    """
    # 404 rather than 403: a 403 would confirm that ticket #N exists, which leaks the
    # size and shape of a queue the user is not allowed to read.
    ticket = get_object_or_404(
        visible_tickets(request.user).select_related("closed_by"),
        pk=pk,
    )
    return render(request, "tickets/ticket_detail.html", {"ticket": ticket})


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def ticket_edit_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit an existing ticket. Handles ``closed_by`` audit field on close/reopen.

    Args:
        request: The HTTP request.
        pk: The ticket primary key.

    Returns:
        Rendered edit form, or redirect to the detail page on success.

    """
    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)

    if request.method == "POST":
        previous_status = ticket.status
        form = TicketEditForm(request.POST, instance=ticket)
        if form.is_valid():
            updated = form.save(commit=False)
            if updated.status == Ticket.Status.CLOSED and previous_status != Ticket.Status.CLOSED:
                updated.closed_by = request.user
            elif updated.status != Ticket.Status.CLOSED and previous_status == Ticket.Status.CLOSED:
                updated.closed_by = None
            updated.save()
            logfire.info(
                "Ticket updated",
                ticket_id=ticket.pk,
                user_id=request.user.id,
                status=updated.status,
                previous_status=previous_status,
            )
            messages.success(request, "Ticket updated.")
            return redirect("tickets:ticket_detail", pk=ticket.pk)
    else:
        form = TicketEditForm(instance=ticket)

    return render(
        request,
        "tickets/ticket_form.html",
        {
            "form": form,
            "ticket": ticket,
            "page_title": f"Edit Ticket #{ticket.pk}",
            "submit_label": "Save Changes",
        },
    )
