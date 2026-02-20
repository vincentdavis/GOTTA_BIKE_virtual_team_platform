"""Views for events app."""

from decimal import Decimal

import logfire
from constance import config
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import team_member_required
from apps.events.forms import EventForm, SquadForm
from apps.events.models import Event, EventSignup, Squad, SquadMember
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider


def _enrich_squad_members(event):
    """Build enriched member data grouped by squad for an event.

    Queries ZP/ZR data for all squad members in one batch, then groups by squad.

    Args:
        event: Event instance to look up squad members for.

    Returns:
        Dict mapping squad pk to list of enriched member dicts.

    """
    all_sms = list(
        SquadMember.objects.filter(squad__event=event, status=SquadMember.Status.MEMBER)
        .select_related("user", "squad")
    )
    if not all_sms:
        return {}

    zwids = [sm.user.zwid for sm in all_sms if sm.user.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}

    by_squad = {}
    for sm in all_sms:
        user = sm.user
        zwid = user.zwid
        zp = zp_by_zwid.get(zwid)
        zr = zr_by_zwid.get(zwid)
        zp_ftp = zp.ftp if zp else None
        zp_weight = zp.weight if zp else None
        wkg = round(Decimal(zp_ftp) / zp_weight, 2) if zp_ftp and zp_weight and zp_weight > 0 else None

        squad_role_id = sm.squad.team_discord_role
        has_discord_role = user.has_discord_role(squad_role_id) if squad_role_id else None

        entry = {
            "user": user,
            "zwid": zwid,
            "gender": user.gender or "",
            "is_race_ready": user.is_race_ready,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "zp_ftp": zp_ftp,
            "wkg": wkg,
            "in_zwiftracing": zr is not None,
            "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "has_discord_role": has_discord_role,
        }
        by_squad.setdefault(sm.squad_id, []).append(entry)

    return by_squad


def _enrich_signups(signups, event=None):
    """Enrich signup queryset with ZP/ZR data and squad assignment for display.

    Args:
        signups: EventSignup queryset with select_related("user").
        event: Optional Event instance to look up squad assignments.

    Returns:
        List of dicts with signup + rider data for template rendering.

    """
    zwids = [s.user.zwid for s in signups if s.user.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}

    squads_by_user: dict[int, list] = {}
    if event:
        for sm in SquadMember.objects.filter(squad__event=event).select_related("squad"):
            squads_by_user.setdefault(sm.user_id, []).append(sm.squad)

    enriched = []
    for signup in signups:
        user = signup.user
        zwid = user.zwid
        zp = zp_by_zwid.get(zwid)
        zr = zr_by_zwid.get(zwid)

        zp_ftp = zp.ftp if zp else None
        zp_weight = zp.weight if zp else None
        wkg = round(Decimal(zp_ftp) / zp_weight, 2) if zp_ftp and zp_weight and zp_weight > 0 else None

        enriched.append({
            "signup": signup,
            "user": user,
            "zwid": zwid,
            "gender": user.gender or "",
            "is_race_ready": user.is_race_ready,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "zp_ftp": zp_ftp,
            "wkg": wkg,
            "in_zwiftracing": zr is not None,
            "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "assigned_squads": squads_by_user.get(user.pk, []),
        })
    return enriched


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
    events = Event.objects.filter(visible=True).annotate(
        signup_count=Count("signups", filter=Q(signups__status="registered")),
    )
    search_query = request.GET.get("q", "").strip()
    if search_query:
        events = events.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))
    user_signup_event_ids = set(
        EventSignup.objects.filter(user=request.user, status=EventSignup.Status.REGISTERED).values_list(
            "event_id", flat=True
        )
    )
    logfire.debug("Event list viewed", user_id=request.user.id, event_count=events.count())
    return render(
        request,
        "events/event_list.html",
        {
            "events": events,
            "search_query": search_query,
            "is_event_admin": request.user.is_event_admin,
            "user_signup_event_ids": user_signup_event_ids,
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
    squads = list(
        event.squads.select_related("captain", "vice_captain").annotate(member_count=Count("squad_members")).all()
    )
    signups = event.signups.select_related("user").all()
    user_signup = event.signups.filter(user=request.user).first()
    enriched_signups = _enrich_signups(signups, event=event) if request.user.is_event_admin else []

    # Attach enriched member data and tooltip to each squad
    squad_members_data = _enrich_squad_members(event) if squads else {}
    user_squad_ids = set(
        SquadMember.objects.filter(squad__event=event, user=request.user).values_list("squad_id", flat=True)
    )
    for squad in squads:
        squad.enriched_members = squad_members_data.get(squad.pk, [])
        names = [m["user"].get_full_name() or m["user"].discord_username for m in squad.enriched_members]
        squad.member_names_tooltip = ", ".join(names) if names else "No members"
        squad.user_is_member = squad.pk in user_squad_ids

    logfire.debug("Event detail viewed", user_id=request.user.id, event_id=pk)
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "races": races,
            "squads": squads,
            "signups": enriched_signups,
            "signup_count": signups.count(),
            "user_signup": user_signup,
            "is_event_admin": request.user.is_event_admin,
            "guild_id": config.GUILD_ID,
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

    squads = event.squads.select_related("captain", "vice_captain").annotate(member_count=Count("squad_members")).all()
    enriched_signups = _enrich_signups(event.signups.select_related("user").all(), event=event)
    return render(
        request,
        "events/event_form.html",
        {
            "form": form,
            "event": event,
            "squads": squads,
            "signups": enriched_signups,
            "page_title": "Edit Event",
            "submit_label": "Save Changes",
        },
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


@login_required
@team_member_required()
@require_POST
def event_signup_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Sign up the current user for an event.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Redirect to event detail page.

    """
    event = get_object_or_404(Event, pk=pk)

    if not event.signups_open:
        messages.error(request, "Signups are not open for this event.")
        return redirect("events:event_detail", pk=pk)

    if event.signups.filter(user=request.user).exists():
        messages.warning(request, "You are already signed up for this event.")
        return redirect("events:event_detail", pk=pk)

    signup_timezone = []
    if event.timezone_options:
        signup_timezone = request.POST.getlist("signup_timezone")
        if event.timezone_required and not signup_timezone:
            messages.error(request, "Please select at least one timezone.")
            return redirect("events:event_detail", pk=pk)
        invalid = [tz for tz in signup_timezone if tz not in event.timezone_options]
        if invalid:
            messages.error(request, "Invalid timezone selection.")
            return redirect("events:event_detail", pk=pk)

    notes = request.POST.get("notes", "").strip()

    EventSignup.objects.create(
        event=event,
        user=request.user,
        signup_timezone=signup_timezone,
        notes=notes,
    )
    logfire.info(
        "Event signup created",
        event_id=pk,
        event_title=event.title,
        user_id=request.user.id,
        signup_timezone=signup_timezone,
    )
    messages.success(request, "You have signed up for this event!")
    return redirect("events:event_detail", pk=pk)


@login_required
@team_member_required()
@require_POST
def event_signup_edit_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Update the current user's signup for an event.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Redirect to event detail page.

    """
    event = get_object_or_404(Event, pk=pk)
    signup = get_object_or_404(EventSignup, event=event, user=request.user, status=EventSignup.Status.REGISTERED)

    signup_timezone = []
    if event.timezone_options:
        signup_timezone = request.POST.getlist("signup_timezone")
        if event.timezone_required and not signup_timezone:
            messages.error(request, "Please select at least one timezone.")
            return redirect("events:event_detail", pk=pk)
        invalid = [tz for tz in signup_timezone if tz not in event.timezone_options]
        if invalid:
            messages.error(request, "Invalid timezone selection.")
            return redirect("events:event_detail", pk=pk)

    signup.signup_timezone = signup_timezone
    signup.notes = request.POST.get("notes", "").strip()
    signup.save(update_fields=["signup_timezone", "notes", "updated_at"])
    logfire.info(
        "Event signup updated",
        event_id=pk,
        event_title=event.title,
        user_id=request.user.id,
        signup_timezone=signup_timezone,
    )
    messages.success(request, "Your signup has been updated.")
    return redirect("events:event_detail", pk=pk)


@login_required
@team_member_required()
@require_POST
def event_signup_delete_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete the current user's signup for an event.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Redirect to event detail page.

    """
    event = get_object_or_404(Event, pk=pk)
    signup = get_object_or_404(EventSignup, event=event, user=request.user, status=EventSignup.Status.REGISTERED)
    signup.delete()
    logfire.info(
        "Event signup deleted",
        event_id=pk,
        event_title=event.title,
        user_id=request.user.id,
    )
    messages.success(request, "Your signup has been removed.")
    return redirect("events:event_detail", pk=pk)


@login_required
@team_member_required()
@require_POST
def event_signup_withdraw_view(request: HttpRequest, event_pk: int, signup_pk: int) -> HttpResponse:
    """Withdraw a signup (admin only).

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        signup_pk: The signup primary key.

    Returns:
        Redirect to event edit page.

    """
    event = get_object_or_404(Event, pk=event_pk)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized signup withdraw attempt",
            event_id=event_pk,
            signup_id=signup_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage signups.")
        return redirect("events:event_detail", pk=event_pk)

    signup = get_object_or_404(EventSignup, pk=signup_pk, event=event)
    signup.status = EventSignup.Status.WITHDRAWN
    signup.save(update_fields=["status", "updated_at"])
    logfire.info(
        "Event signup withdrawn",
        event_id=event_pk,
        signup_id=signup_pk,
        signup_user_id=signup.user_id,
        admin_user_id=request.user.id,
    )
    messages.success(request, f"Signup for {signup.user} has been withdrawn.")
    return redirect("events:event_edit", pk=event_pk)


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def squad_create_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Create a new squad for an event.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.

    Returns:
        Rendered form or redirect on success.

    """
    event = get_object_or_404(Event, pk=event_pk)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized squad creation attempt",
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    if request.method == "POST":
        form = SquadForm(request.POST)
        if form.is_valid():
            squad = form.save(commit=False)
            squad.event = event
            squad.created_by = request.user
            squad.save()
            logfire.info(
                "Squad created",
                squad_id=squad.pk,
                squad_name=squad.name,
                event_id=event_pk,
                user_id=request.user.id,
            )
            messages.success(request, f'Squad "{squad.name}" created successfully!')
            return redirect("events:event_detail", pk=event_pk)
    else:
        form = SquadForm()

    return render(
        request,
        "events/squad_form.html",
        {
            "form": form,
            "event": event,
            "page_title": "Add Squad",
            "submit_label": "Create Squad",
        },
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def squad_edit_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Edit an existing squad.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        Rendered form or redirect on success.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized squad edit attempt",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    if request.method == "POST":
        form = SquadForm(request.POST, instance=squad)
        if form.is_valid():
            form.save()
            logfire.info(
                "Squad updated",
                squad_id=squad_pk,
                squad_name=squad.name,
                event_id=event_pk,
                user_id=request.user.id,
            )
            messages.success(request, f'Squad "{squad.name}" updated successfully!')
            return redirect("events:event_detail", pk=event_pk)
    else:
        form = SquadForm(instance=squad)

    return render(
        request,
        "events/squad_form.html",
        {
            "form": form,
            "event": event,
            "squad": squad,
            "page_title": "Edit Squad",
            "submit_label": "Save Changes",
        },
    )


@login_required
@team_member_required()
@require_POST
def squad_delete_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Delete a squad.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        Redirect to event detail.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized squad delete attempt",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    squad_name = squad.name
    squad.delete()
    logfire.info(
        "Squad deleted",
        squad_id=squad_pk,
        squad_name=squad_name,
        event_id=event_pk,
        user_id=request.user.id,
    )
    messages.success(request, f'Squad "{squad_name}" deleted successfully!')
    return redirect("events:event_detail", pk=event_pk)


@login_required
@team_member_required()
@require_POST
def squad_assign_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Assign or unassign a signed-up user to a squad.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.

    Returns:
        Redirect to event detail page.

    """
    event = get_object_or_404(Event, pk=event_pk)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized squad assign attempt",
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to assign squads.")
        return redirect("events:event_detail", pk=event_pk)

    signup_id = request.POST.get("signup_id")
    squad_id = request.POST.get("squad_id")

    if not signup_id or squad_id is None:
        messages.error(request, "Missing signup or squad selection.")
        return redirect("events:event_detail", pk=event_pk)

    signup = get_object_or_404(EventSignup, pk=signup_id, event=event)
    squad_id = int(squad_id)

    if squad_id == 0:
        # Unassign from a specific squad or all squads
        remove_squad_id = request.POST.get("remove_squad_id")
        if remove_squad_id:
            remove_squad = get_object_or_404(Squad, pk=int(remove_squad_id), event=event)
            deleted, _ = SquadMember.objects.filter(squad=remove_squad, user=signup.user).delete()
            if deleted:
                logfire.info(
                    "Squad assignment removed",
                    event_id=event_pk,
                    squad_id=remove_squad.pk,
                    squad_name=remove_squad.name,
                    user_id=signup.user_id,
                    admin_user_id=request.user.id,
                )
                messages.success(request, f"{signup.user} removed from {remove_squad.name}.")
            else:
                messages.info(request, f"{signup.user} was not in {remove_squad.name}.")
        else:
            deleted, _ = SquadMember.objects.filter(squad__event=event, user=signup.user).delete()
            if deleted:
                logfire.info(
                    "All squad assignments removed",
                    event_id=event_pk,
                    user_id=signup.user_id,
                    admin_user_id=request.user.id,
                )
                messages.success(request, f"{signup.user} removed from all squads.")
            else:
                messages.info(request, f"{signup.user} was not assigned to any squad.")
    else:
        squad = get_object_or_404(Squad, pk=squad_id, event=event)
        SquadMember.objects.update_or_create(
            squad=squad,
            user=signup.user,
            defaults={"status": SquadMember.Status.MEMBER},
        )
        logfire.info(
            "Squad assignment created",
            event_id=event_pk,
            squad_id=squad.pk,
            squad_name=squad.name,
            user_id=signup.user_id,
            admin_user_id=request.user.id,
        )
        messages.success(request, f"{signup.user} assigned to {squad.name}.")

    # HTMX: return just the updated squad cell instead of full page reload
    if request.headers.get("HX-Request"):
        assigned_squads = list(Squad.objects.filter(
            pk__in=SquadMember.objects.filter(squad__event=event, user=signup.user).values_list("squad_id", flat=True),
        ))
        all_squads = list(event.squads.all())
        return render(
            request,
            "events/_squad_cell.html",
            {
                "assigned_squads": assigned_squads,
                "squads": all_squads,
                "event_pk": event_pk,
                "signup_id": signup.pk,
            },
        )

    return redirect("events:event_detail", pk=event_pk)
