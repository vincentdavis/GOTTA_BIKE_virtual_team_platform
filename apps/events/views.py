"""Views for events app."""

import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from zoneinfo import available_timezones

import logfire
from constance import config
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import discord_permission_required, team_member_required
from apps.accounts.discord_service import add_discord_role, remove_discord_role
from apps.accounts.models import Permissions, User
from apps.events.forms import EventForm, EventRoleSetupForm, SquadForm
from apps.events.models import (
    ZR_CATEGORY_ORDER,
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    EventSignup,
    Squad,
    SquadMember,
)
from apps.events.tz_utils import (
    TIMEZONE_CHOICES,
    convert_blocked_cells_to_utc,
    convert_grid_to_local,
    convert_local_to_utc,
)
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider

CATEGORY_COLUMNS = ["A+", "A", "B", "C", "D", "E"]


def _can_manage_event_roles(user: User, event: Event) -> bool:
    """Check if a user can manage Discord roles for an event.

    Allowed if the user has the assign_roles permission OR holds the event's head captain Discord role.

    Args:
        user: The requesting user.
        event: The event to check against.

    Returns:
        True if the user can manage roles for this event.

    """
    if user.has_permission(Permissions.ASSIGN_ROLES):
        return True
    return bool(event.head_captain_role_id and user.has_discord_role(event.head_captain_role_id))


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
            "is_extra_verified": user.is_extra_verified,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "zp_ftp": zp_ftp,
            "zp_rank": zp.rank if zp else None,
            "wkg": wkg,
            "in_zwiftracing": zr is not None,
            "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "zr_age": getattr(zr, "age", "") or "" if zr else "",
            "zr_current_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "zr_current_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_max30_rating": getattr(zr, "race_max30_rating", None) if zr else None,
            "zr_max30_category": getattr(zr, "race_max30_category", "") or "" if zr else "",
            "zr_max90_rating": getattr(zr, "race_max90_rating", None) if zr else None,
            "zr_max90_category": getattr(zr, "race_max90_category", "") or "" if zr else "",
            "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
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

    event_role_id = str(event.event_role) if event and event.event_role else ""

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
            "is_extra_verified": user.is_extra_verified,
            "has_event_role": user.has_discord_role(event_role_id) if event_role_id else False,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "zp_ftp": zp_ftp,
            "wkg": wkg,
            "in_zwiftracing": zr is not None,
            "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "zr_age": getattr(zr, "age", "") or "" if zr else "",
            "zr_current_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "zr_current_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_max30_rating": getattr(zr, "race_max30_rating", None) if zr else None,
            "zr_max30_category": getattr(zr, "race_max30_category", "") or "" if zr else "",
            "zr_max90_rating": getattr(zr, "race_max90_rating", None) if zr else None,
            "zr_max90_category": getattr(zr, "race_max90_category", "") or "" if zr else "",
            "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
            "assigned_squads": squads_by_user.get(user.pk, []),
        })
    return enriched


@require_GET
@login_required
@team_member_required()
def my_events_view(request: HttpRequest) -> HttpResponse:
    """Display the current user's event signups with squad and availability info.

    Args:
        request: The HTTP request.

    Returns:
        Rendered my events page.

    """
    signups = (
        EventSignup.objects.filter(user=request.user, status=EventSignup.Status.REGISTERED)
        .select_related("event")
        .order_by("-event__start_date")
    )
    squad_memberships = (
        SquadMember.objects.filter(user=request.user, status=SquadMember.Status.MEMBER)
        .select_related("squad", "squad__event")
    )
    squads_by_event: dict[int, list] = {}
    for sm in squad_memberships:
        squads_by_event.setdefault(sm.squad.event_id, []).append(sm.squad)

    signup_event_ids = [s.event_id for s in signups]
    squad_event_ids = [eid for eid in squads_by_event if eid not in signup_event_ids]

    grids = AvailabilityGrid.objects.filter(
        squad__event_id__in=signup_event_ids + squad_event_ids,
        status__in=[AvailabilityGrid.Status.PUBLISHED, AvailabilityGrid.Status.CLOSED],
    ).select_related("squad")
    grids_by_squad: dict[int, list] = {}
    for grid in grids:
        grids_by_squad.setdefault(grid.squad_id, []).append(grid)

    responded_grid_ids = set(
        AvailabilityResponse.objects.filter(
            user=request.user,
            grid__in=grids,
        ).values_list("grid_id", flat=True)
    )

    all_squad_ids = [sq.pk for squads in squads_by_event.values() for sq in squads]
    members_by_squad: dict[int, list] = {}
    if all_squad_ids:
        all_sms = list(
            SquadMember.objects.filter(squad_id__in=all_squad_ids, status=SquadMember.Status.MEMBER)
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
        )
        zwids = [sm.user.zwid for sm in all_sms if sm.user.zwid]
        zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
        zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}
        for sm in all_sms:
            user = sm.user
            zwid = user.zwid
            zp = zp_by_zwid.get(zwid)
            zr = zr_by_zwid.get(zwid)
            members_by_squad.setdefault(sm.squad_id, []).append({
                "user": user,
                "zwid": zwid,
                "is_race_ready": user.is_race_ready,
                "is_extra_verified": user.is_extra_verified,
                "in_zwiftpower": zp is not None,
                "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
                "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
                "in_zwiftracing": zr is not None,
                "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
                "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
                "zr_age": getattr(zr, "age", "") or "" if zr else "",
                "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
            })

    events_data = []
    for signup in signups:
        event = signup.event
        event_squads = squads_by_event.get(event.pk, [])
        squad_data = []
        for squad in event_squads:
            squad_grids = grids_by_squad.get(squad.pk, [])
            pending_count = 0
            for g in squad_grids:
                g.user_responded = g.pk in responded_grid_ids
                if g.is_published and not g.user_responded:
                    pending_count += 1
            squad_data.append({
                "squad": squad,
                "grids": squad_grids,
                "pending_availability_count": pending_count,
                "members": members_by_squad.get(squad.pk, []),
            })
        event_pending = sum(sq["pending_availability_count"] for sq in squad_data)
        has_grids = any(sq["grids"] for sq in squad_data)
        events_data.append({
            "event": event,
            "squads": squad_data,
            "pending_availability_count": event_pending,
            "has_availability_grids": has_grids,
        })

    logfire.debug("My events viewed", user_id=request.user.id, event_count=len(events_data))
    return render(
        request,
        "events/my_events.html",
        {
            "events_data": events_data,
            "guild_id": config.GUILD_ID,
        },
    )


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
    events = (
        Event.objects
        .filter(visible=True)
        .annotate(
            signup_count=Count("signups", filter=Q(signups__status="registered")),
        )
        .order_by("start_date")
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
            "today": timezone.now().date(),
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
    published_grids_by_squad: dict[int, list] = {}
    for grid in AvailabilityGrid.objects.filter(squad__event=event, status__in=["published", "closed"]):
        published_grids_by_squad.setdefault(grid.squad_id, []).append(grid)

    for squad in squads:
        squad.enriched_members = squad_members_data.get(squad.pk, [])
        names = [m["user"].get_full_name() or m["user"].discord_username for m in squad.enriched_members]
        squad.member_names_tooltip = ", ".join(names) if names else "No members"
        squad.user_is_member = squad.pk in user_squad_ids
        squad.published_grids = published_grids_by_squad.get(squad.pk, [])

    # Aggregate ZP and ZR category counts per squad
    zp_by_squad = []
    zp_totals = defaultdict(int)
    zp_total_all = 0
    zr_by_squad = []
    zr_totals = defaultdict(int)
    zr_total_all = 0
    all_ranks = []
    all_ratings = []
    for squad in squads:
        zp_counts = defaultdict(int)
        zr_counts = defaultdict(int)
        squad_ranks = []
        squad_ratings = []
        for member in squad.enriched_members:
            zp_cat = member["zp_category_w"] or member["zp_category"] or "-"
            zp_counts[zp_cat] += 1
            zr_cat = member["zr_category"] or "-"
            zr_counts[zr_cat] += 1
            if member["zp_rank"] is not None:
                squad_ranks.append(member["zp_rank"])
            if member["zr_rating"] is not None:
                squad_ratings.append(member["zr_rating"])
        avg_rank = round(sum(squad_ranks) / len(squad_ranks), 1) if squad_ranks else None
        avg_rating = round(sum(squad_ratings) / len(squad_ratings), 1) if squad_ratings else None
        all_ranks.extend(squad_ranks)
        all_ratings.extend(squad_ratings)
        zp_total = sum(zp_counts.values())
        zp_by_squad.append({"squad": squad, "counts": dict(zp_counts), "total": zp_total, "avg_rank": avg_rank})
        for cat, count in zp_counts.items():
            zp_totals[cat] += count
        zp_total_all += zp_total
        zr_total = sum(zr_counts.values())
        zr_by_squad.append({"squad": squad, "counts": dict(zr_counts), "total": zr_total, "avg_rating": avg_rating})
        for cat, count in zr_counts.items():
            zr_totals[cat] += count
        zr_total_all += zr_total
    zp_totals = dict(zp_totals)
    zr_totals = dict(zr_totals)
    zp_avg_rank_all = round(sum(all_ranks) / len(all_ranks), 1) if all_ranks else None
    zr_avg_rating_all = round(sum(all_ratings) / len(all_ratings), 1) if all_ratings else None

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
            "zp_category_columns": CATEGORY_COLUMNS,
            "zp_by_squad": zp_by_squad,
            "zp_totals": zp_totals,
            "zp_total_all": zp_total_all,
            "zp_avg_rank_all": zp_avg_rank_all,
            "zr_category_columns": ZR_CATEGORY_ORDER,
            "zr_by_squad": zr_by_squad,
            "zr_totals": zr_totals,
            "zr_total_all": zr_total_all,
            "zr_avg_rating_all": zr_avg_rating_all,
            "can_manage_roles": _can_manage_event_roles(request.user, event),
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
        form = EventForm(request.POST, request.FILES)
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
        form = EventForm(request.POST, request.FILES, instance=event)
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

    enriched_signups = _enrich_signups(
        event.signups.select_related("user").all(), event=event
    )

    # Build read-only role display names
    from apps.team.models import DiscordRole

    role_display = {}
    for field_name, field_value in [
        ("head_captain_role_id", event.head_captain_role_id),
        ("event_role", event.event_role),
    ]:
        if field_value and field_value != 0:
            role = DiscordRole.objects.filter(role_id=str(field_value)).first()
            role_display[field_name] = f"@{role.name}" if role else f"Unknown ({field_value})"
        else:
            role_display[field_name] = "(none)"

    return render(
        request,
        "events/event_form.html",
        {
            "form": form,
            "event": event,
            "signups": enriched_signups,
            "page_title": "Edit Event",
            "submit_label": "Save Changes",
            "can_manage_roles": _can_manage_event_roles(request.user, event),
            "role_display": role_display,
        },
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def event_role_setup_view(request: HttpRequest, pk: int) -> HttpResponse:
    """View and optionally edit Discord role settings for an event.

    Users with event_admin, assign_roles, or the event's head captain role can view.
    Users with assign_roles or head captain role can edit.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        Rendered role setup form or redirect on success.

    Raises:
        PermissionDenied: If the user lacks permission.

    """
    event = get_object_or_404(Event, pk=pk)
    can_edit = _can_manage_event_roles(request.user, event)
    if not can_edit and not request.user.has_permission(Permissions.EVENT_ADMIN):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied

    if request.method == "POST" and can_edit:
        form = EventRoleSetupForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            logfire.info(
                "Event role setup updated",
                event_id=pk,
                event_title=event.title,
                user_id=request.user.id,
            )
            messages.success(request, "Role setup updated successfully!")
            return redirect("events:event_role_setup", pk=pk)
    else:
        form = EventRoleSetupForm(instance=event)

    # Look up display names for read-only mode
    from apps.team.models import DiscordRole

    role_display = {}
    for field_name, field_value in [
        ("head_captain_role_id", event.head_captain_role_id),
        ("event_role", event.event_role),
    ]:
        if field_value and field_value != 0:
            role = DiscordRole.objects.filter(role_id=str(field_value)).first()
            role_display[field_name] = f"@{role.name}" if role else f"Unknown ({field_value})"
        else:
            role_display[field_name] = "(none)"

    try:
        allowed_prefixes = json.loads(config.EVENT_ROLE_PREFIXES)
    except (json.JSONDecodeError, ValueError, TypeError):
        allowed_prefixes = ["$", ">", "¡", "~", "^"]

    return render(
        request,
        "events/event_role_setup.html",
        {
            "form": form,
            "event": event,
            "can_edit": can_edit,
            "role_display": role_display,
            "allowed_prefixes": "  ".join(allowed_prefixes),
        },
    )


@login_required
@team_member_required()
@require_GET
def squad_manage_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Manage squads for an event.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.

    Returns:
        Rendered squad management page.

    """
    event = get_object_or_404(Event, pk=event_pk)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized squad manage attempt",
            event_id=event_pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    squads = list(
        event.squads.select_related("captain", "vice_captain").annotate(member_count=Count("squad_members")).all()
    )

    # Build ID→name lookups for Discord channels and roles
    channel_ids = set()
    role_ids = set()
    for s in squads:
        if s.discord_channel_id:
            channel_ids.add(str(s.discord_channel_id))
        if s.audio_channel_id:
            channel_ids.add(str(s.audio_channel_id))
        if s.team_discord_role:
            role_ids.add(str(s.team_discord_role))

    from apps.team.models import DiscordChannel, DiscordRole

    channel_names = dict(
        DiscordChannel.objects.filter(channel_id__in=channel_ids).values_list("channel_id", "name")
    ) if channel_ids else {}
    role_names = dict(
        DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")
    ) if role_ids else {}

    # Attach availability grids (published/closed) grouped by squad
    grids_by_squad: dict[int, list] = {}
    for grid in AvailabilityGrid.objects.filter(
        squad__event=event,
        status__in=[AvailabilityGrid.Status.PUBLISHED, AvailabilityGrid.Status.CLOSED],
    ):
        grids_by_squad.setdefault(grid.squad_id, []).append(grid)

    for s in squads:
        s.channel_name = channel_names.get(str(s.discord_channel_id), "") if s.discord_channel_id else ""
        s.audio_name = channel_names.get(str(s.audio_channel_id), "") if s.audio_channel_id else ""
        s.role_name = role_names.get(str(s.team_discord_role), "") if s.team_discord_role else ""
        s.active_grids = grids_by_squad.get(s.pk, [])

    return render(
        request,
        "events/squad_manage.html",
        {
            "event": event,
            "squads": squads,
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
    removed_squads, _ = SquadMember.objects.filter(squad__event=event, user=request.user).delete()
    signup.delete()
    logfire.info(
        "Event signup deleted",
        event_id=pk,
        event_title=event.title,
        user_id=request.user.id,
        squad_memberships_removed=removed_squads,
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
    removed_squads, _ = SquadMember.objects.filter(squad__event=event, user=signup.user).delete()
    signup.status = EventSignup.Status.WITHDRAWN
    signup.save(update_fields=["status", "updated_at"])
    logfire.info(
        "Event signup withdrawn",
        event_id=event_pk,
        signup_id=signup_pk,
        signup_user_id=signup.user_id,
        admin_user_id=request.user.id,
        squad_memberships_removed=removed_squads,
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
        form = SquadForm(request.POST, event_prefix=event.prefix or "")
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
        form = SquadForm(event_prefix=event.prefix or "")

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
        form = SquadForm(request.POST, instance=squad, event_prefix=event.prefix or "")
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
            return redirect("events:squad_manage", event_pk=event_pk)
    else:
        form = SquadForm(instance=squad, event_prefix=event.prefix or "")

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
def squad_availability_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Manage availability grids for a squad.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        Rendered availability management page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized squad availability access attempt",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    availability_grids = squad.availability_grids.all()

    return render(
        request,
        "events/squad_availability.html",
        {
            "event": event,
            "squad": squad,
            "availability_grids": availability_grids,
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
        assigned_squads = list(
            Squad.objects.filter(
                pk__in=SquadMember.objects.filter(squad__event=event, user=signup.user).values_list(
                    "squad_id", flat=True
                ),
            )
        )
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


@login_required
@team_member_required()
@require_POST
def squad_toggle_role_view(request: HttpRequest, event_pk: int, squad_pk: int, user_id: int) -> HttpResponse:
    """Toggle a squad's Discord role for a member.

    Adds the role if the member doesn't have it, removes it if they do.
    Accessible to users with assign_roles permission or the event's head captain role.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        user_id: The target user primary key.

    Returns:
        Redirect to event detail page.

    Raises:
        PermissionDenied: If user lacks permission.

    """
    event = get_object_or_404(Event, pk=event_pk)
    if not _can_manage_event_roles(request.user, event):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You need Assign Roles permission or the Head Captain role for this event.")

    squad = get_object_or_404(Squad, pk=squad_pk, event_id=event_pk)
    role_id = squad.team_discord_role
    if not role_id:
        messages.error(request, "This squad has no Discord role configured.")
        return redirect("events:event_detail", pk=event_pk)

    target_user = get_object_or_404(User, pk=user_id)
    if not target_user.discord_id:
        messages.error(request, f"{target_user} has no linked Discord account.")
        return redirect("events:event_detail", pk=event_pk)

    role_id_str = str(role_id)
    if target_user.has_discord_role(role_id):
        success = remove_discord_role(target_user.discord_id, role_id_str)
        if success:
            roles = dict(target_user.discord_roles or {})
            roles.pop(role_id_str, None)
            target_user.discord_roles = roles
            target_user.save(update_fields=["discord_roles"])
            logfire.info(
                "Discord role removed from squad member",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
                admin_user_id=request.user.id,
            )
            messages.success(request, f"Removed Discord role from {target_user}.")
        else:
            logfire.error(
                "Failed to remove Discord role",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
            )
            messages.error(request, "Failed to remove Discord role. Check bot token configuration.")
    else:
        success = add_discord_role(target_user.discord_id, role_id_str)
        if success:
            roles = dict(target_user.discord_roles or {})
            roles[role_id_str] = squad.name
            target_user.discord_roles = roles
            target_user.save(update_fields=["discord_roles"])
            logfire.info(
                "Discord role added to squad member",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
                admin_user_id=request.user.id,
            )
            messages.success(request, f"Added Discord role to {target_user}.")
        else:
            logfire.error(
                "Failed to add Discord role",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
            )
            messages.error(request, "Failed to add Discord role. Check bot token configuration.")

    if request.headers.get("HX-Request"):
        has_role = target_user.has_discord_role(role_id)
        response = render(
            request,
            "events/_squad_role_cell.html",
            {
                "event_pk": event_pk,
                "squad_pk": squad_pk,
                "member_user_pk": user_id,
                "has_role": has_role,
            },
        )
        stored = messages.get_messages(request)
        msg_list = [{"message": str(m), "tags": m.tags} for m in stored]
        if msg_list:
            response["HX-Trigger"] = json.dumps({"showToast": msg_list})
        return response

    return redirect("events:event_detail", pk=event_pk)


@login_required
@team_member_required()
@require_POST
def squad_toggle_captain_role_view(request: HttpRequest, event_pk: int, squad_pk: int, user_id: int) -> HttpResponse:
    """Toggle a squad's captain Discord role for a member.

    Adds the role if the member doesn't have it, removes it if they do.
    Accessible to users with assign_roles permission or the event's head captain role.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        user_id: The target user primary key.

    Returns:
        Redirect to manage roles page or HTMX partial.

    Raises:
        PermissionDenied: If user lacks permission.

    """
    event = get_object_or_404(Event, pk=event_pk)
    if not _can_manage_event_roles(request.user, event):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You need Assign Roles permission or the Head Captain role for this event.")

    squad = get_object_or_404(Squad, pk=squad_pk, event_id=event_pk)
    role_id = squad.discord_captain_role
    if not role_id:
        messages.error(request, "This squad has no captain Discord role configured.")
        return redirect("events:manage_roles", event_pk=event_pk)

    target_user = get_object_or_404(User, pk=user_id)
    if not target_user.discord_id:
        messages.error(request, f"{target_user} has no linked Discord account.")
        return redirect("events:manage_roles", event_pk=event_pk)

    role_id_str = str(role_id)
    if target_user.has_discord_role(role_id):
        success = remove_discord_role(target_user.discord_id, role_id_str)
        if success:
            roles = dict(target_user.discord_roles or {})
            roles.pop(role_id_str, None)
            target_user.discord_roles = roles
            target_user.save(update_fields=["discord_roles"])
            logfire.info(
                "Captain Discord role removed from squad member",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
                admin_user_id=request.user.id,
            )
            messages.success(request, f"Removed captain role from {target_user}.")
        else:
            logfire.error(
                "Failed to remove captain Discord role",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
            )
            messages.error(request, "Failed to remove captain role. Check bot token configuration.")
    else:
        success = add_discord_role(target_user.discord_id, role_id_str)
        if success:
            roles = dict(target_user.discord_roles or {})
            roles[role_id_str] = f"{squad.name} Captain"
            target_user.discord_roles = roles
            target_user.save(update_fields=["discord_roles"])
            logfire.info(
                "Captain Discord role added to squad member",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
                admin_user_id=request.user.id,
            )
            messages.success(request, f"Added captain role to {target_user}.")
        else:
            logfire.error(
                "Failed to add captain Discord role",
                event_id=event_pk,
                squad_id=squad_pk,
                target_user_id=user_id,
                role_id=role_id_str,
            )
            messages.error(request, "Failed to add captain role. Check bot token configuration.")

    if request.headers.get("HX-Request"):
        has_role = target_user.has_discord_role(role_id)
        response = render(
            request,
            "events/_squad_captain_role_cell.html",
            {
                "event_pk": event_pk,
                "squad_pk": squad_pk,
                "member_user_pk": user_id,
                "has_role": has_role,
            },
        )
        stored = messages.get_messages(request)
        msg_list = [{"message": str(m), "tags": m.tags} for m in stored]
        if msg_list:
            response["HX-Trigger"] = json.dumps({"showToast": msg_list})
        return response

    return redirect("events:manage_roles", event_pk=event_pk)


@require_http_methods(["GET", "POST"])
@login_required
@team_member_required()
def availability_create_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Display the availability grid builder or save a new grid.

    GET: renders the builder UI.
    POST: accepts JSON body, validates, creates an AvailabilityGrid, returns JSON.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        Rendered availability builder page (GET) or JsonResponse (POST).

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized availability builder access",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        if request.method == "POST":
            return JsonResponse({"error": "Permission denied."}, status=403)
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    if request.method == "POST":
        return _handle_availability_save(request, event, squad)

    user_tz = getattr(request.user, "timezone", "") or "UTC"
    logfire.debug("Availability builder viewed", user_id=request.user.id, event_id=event_pk, squad_id=squad_pk)
    return render(
        request,
        "events/availability_builder.html",
        {
            "event": event,
            "squad": squad,
            "timezone_choices_json": json.dumps(TIMEZONE_CHOICES),
            "user_timezone": user_tz,
        },
    )


def _handle_availability_save(request: HttpRequest, event: Event, squad: Squad) -> JsonResponse:
    """Validate and persist an AvailabilityGrid from a JSON POST body.

    Args:
        request: The HTTP request with JSON body.
        event: The parent event.
        squad: The squad to attach the grid to.

    Returns:
        JsonResponse with grid id on success, or error details on failure.

    """
    hhmm_re = re.compile(r"^\d{2}:\d{2}$")

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    # --- Parse & validate fields ---
    title = str(data.get("title", "")).strip()

    try:
        start_date = date.fromisoformat(str(data.get("start_date", "")))
        end_date = date.fromisoformat(str(data.get("end_date", "")))
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid or missing start_date / end_date."}, status=400)

    if start_date > end_date:
        return JsonResponse({"error": "start_date must be on or before end_date."}, status=400)
    if (end_date - start_date).days > 31:
        return JsonResponse({"error": "Date range cannot exceed 31 days."}, status=400)

    start_time = str(data.get("start_time", ""))
    end_time = str(data.get("end_time", ""))
    if not hhmm_re.match(start_time) or not hhmm_re.match(end_time):
        return JsonResponse({"error": "start_time and end_time must be HH:MM format."}, status=400)
    if start_time >= end_time:
        return JsonResponse({"error": "start_time must be before end_time."}, status=400)

    try:
        slot_duration = int(data.get("slot_duration", 0))
    except (ValueError, TypeError):
        return JsonResponse({"error": "slot_duration must be an integer."}, status=400)
    if slot_duration not in (15, 30, 60):
        return JsonResponse({"error": "slot_duration must be 15, 30, or 60."}, status=400)

    blocked_cells = data.get("blocked_cells", [])
    if not isinstance(blocked_cells, list):
        return JsonResponse({"error": "blocked_cells must be a list."}, status=400)

    # --- Timezone handling ---
    grid_tz = str(data.get("timezone", "UTC")).strip() or "UTC"
    if grid_tz != "UTC" and grid_tz not in available_timezones():
        return JsonResponse({"error": f"Invalid timezone: {grid_tz}"}, status=400)

    # --- Optional expires date ---
    expires = None
    raw_expires = data.get("expires")
    if raw_expires:
        try:
            expires = date.fromisoformat(str(raw_expires))
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid expires date."}, status=400)

    if grid_tz != "UTC":
        start_date, end_date, start_time, end_time = convert_local_to_utc(
            start_date,
            end_date,
            start_time,
            end_time,
            grid_tz,
        )
        blocked_cells = convert_blocked_cells_to_utc(blocked_cells, grid_tz, slot_duration)

    grid = AvailabilityGrid.objects.create(
        squad=squad,
        title=title,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        slot_duration=slot_duration,
        grid_timezone=grid_tz,
        blocked_cells=blocked_cells,
        max_races_question=bool(data.get("max_races_question", False)),
        rest_days_question=bool(data.get("rest_days_question", False)),
        expires=expires,
        status=AvailabilityGrid.Status.DRAFT,
        created_by=request.user,
    )

    logfire.info(
        "Availability grid created",
        grid_id=str(grid.id),
        squad_id=squad.pk,
        event_id=event.pk,
        user_id=request.user.id,
    )
    return JsonResponse({"id": str(grid.id), "status": "ok"})


@login_required
@team_member_required()
@require_POST
def availability_status_view(request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str) -> HttpResponse:
    """Change the status of an availability grid (publish or close).

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.

    Returns:
        Redirect to squad edit page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if not request.user.is_event_admin and not request.user.is_superuser:
        logfire.warning(
            "Unauthorized availability status change",
            grid_id=str(grid.id),
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    new_status = request.POST.get("status", "")
    allowed_transitions = {
        AvailabilityGrid.Status.DRAFT: AvailabilityGrid.Status.PUBLISHED,
        AvailabilityGrid.Status.PUBLISHED: AvailabilityGrid.Status.CLOSED,
    }

    expected = allowed_transitions.get(grid.status)
    if expected is None or new_status != expected:
        messages.error(request, f'Cannot change status from "{grid.get_status_display()}" to "{new_status}".')
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)

    grid.status = new_status
    grid.save(update_fields=["status", "updated_at"])

    logfire.info(
        "Availability grid status changed",
        grid_id=str(grid.id),
        new_status=new_status,
        squad_id=squad_pk,
        event_id=event_pk,
        user_id=request.user.id,
    )
    messages.success(request, f'Grid "{grid.title}" is now {grid.get_status_display().lower()}.')
    return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)


@require_http_methods(["GET", "POST"])
@login_required
@team_member_required()
def availability_respond_view(request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str) -> HttpResponse:
    """Display the availability response form or save a member's response.

    GET: renders the response grid pre-populated with any existing response.
    POST: accepts JSON body with available_cells, creates/updates AvailabilityResponse.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.

    Returns:
        Rendered response page (GET) or JsonResponse (POST).

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if grid.status != AvailabilityGrid.Status.PUBLISHED:
        messages.error(request, "This availability grid is not open for responses.")
        return redirect("events:event_detail", pk=event_pk)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        available_cells = data.get("available_cells", [])
        if not isinstance(available_cells, list):
            return JsonResponse({"error": "available_cells must be a list."}, status=400)

        defaults = {"available_cells": available_cells}

        if grid.max_races_question:
            raw_max = data.get("max_races")
            if raw_max is None or raw_max == "":
                return JsonResponse({"error": "Please answer: max number of races."}, status=400)
            try:
                max_races_val = int(raw_max)
            except (ValueError, TypeError):
                return JsonResponse({"error": "Max races must be a non-negative integer."}, status=400)
            if max_races_val < 0:
                return JsonResponse({"error": "Max races must be a non-negative integer."}, status=400)
            defaults["max_races"] = max_races_val

        if grid.rest_days_question:
            raw_rest = data.get("rest_days")
            if raw_rest is None or raw_rest == "":
                return JsonResponse({"error": "Please answer: rest days between races."}, status=400)
            try:
                rest_days_val = int(raw_rest)
            except (ValueError, TypeError):
                return JsonResponse({"error": "Rest days must be a non-negative integer."}, status=400)
            if rest_days_val < 0:
                return JsonResponse({"error": "Rest days must be a non-negative integer."}, status=400)
            defaults["rest_days"] = rest_days_val

        AvailabilityResponse.objects.update_or_create(
            grid=grid,
            user=request.user,
            defaults=defaults,
        )
        logfire.info(
            "Availability response saved",
            grid_id=str(grid.id),
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
            cell_count=len(available_cells),
        )
        return JsonResponse({"status": "ok"})

    existing_response = AvailabilityResponse.objects.filter(grid=grid, user=request.user).first()

    # Determine display timezone: user profile → grid timezone → UTC
    user_tz = getattr(request.user, "timezone", "") or ""
    display_tz = user_tz or grid.grid_timezone or "UTC"
    tz_is_default = not user_tz

    grid_data = convert_grid_to_local(grid.dates, grid.time_slots, grid.blocked_cells, display_tz)

    # Convert existing response UTC keys → local keys
    existing_local_keys: list[str] = []
    if existing_response:
        for cell in existing_response.available_cells:
            utc_key = f"{cell['date']}|{cell['time']}"
            local_key = grid_data["reverse_map"].get(utc_key)
            if local_key:
                existing_local_keys.append(local_key)

    logfire.debug(
        "Availability respond page viewed",
        grid_id=str(grid.id),
        user_id=request.user.id,
        event_id=event_pk,
        squad_id=squad_pk,
        display_tz=display_tz,
    )
    return render(
        request,
        "events/availability_respond.html",
        {
            "event": event,
            "squad": squad,
            "grid": grid,
            "display_dates_json": json.dumps(grid_data["display_dates"]),
            "display_time_slots_json": json.dumps(grid_data["display_time_slots"]),
            "display_blocked_json": json.dumps(sorted(grid_data["display_blocked"])),
            "existing_local_keys_json": json.dumps(existing_local_keys),
            "cell_utc_map_json": json.dumps(grid_data["cell_map"]),
            "valid_cells_json": json.dumps(sorted(grid_data["valid_cells"])),
            "display_timezone": display_tz,
            "tz_is_default": tz_is_default,
            "existing_max_races": existing_response.max_races if existing_response else None,
            "existing_rest_days": existing_response.rest_days if existing_response else None,
        },
    )


@require_GET
@login_required
@team_member_required()
def availability_results_view(request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str) -> HttpResponse:
    """Display aggregated availability results as a heatmap.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.

    Returns:
        Rendered results heatmap page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if grid.status not in (AvailabilityGrid.Status.PUBLISHED, AvailabilityGrid.Status.CLOSED):
        messages.error(request, "Results are not available for this grid.")
        return redirect("events:event_detail", pk=event_pk)

    responses = list(AvailabilityResponse.objects.filter(grid=grid).select_related("user"))
    total_responders = len(responses)

    # Enrich responders with ZP/ZR data
    responder_users = [r.user for r in responses]
    zwids = [u.zwid for u in responder_users if u.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}

    enriched_responders = []
    user_by_id: dict[int, dict] = {}
    response_by_user: dict[int, AvailabilityResponse] = {r.user.pk: r for r in responses}
    for user in responder_users:
        zp = zp_by_zwid.get(user.zwid)
        zr = zr_by_zwid.get(user.zwid)
        display_name = user.get_full_name() or user.discord_username
        zp_cat = ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else ""
        zp_cat_w = ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else ""
        zr_cat = getattr(zr, "race_current_category", "") or "" if zr else ""
        zr_rating = getattr(zr, "race_current_rating", None) if zr else None
        zr_phenotype = getattr(zr, "phenotype_value", "") or "" if zr else ""
        zr_age = getattr(zr, "age", "") or "" if zr else ""
        resp = response_by_user.get(user.pk)
        entry = {
            "user": user,
            "display_name": display_name,
            "zwid": user.zwid,
            "is_race_ready": user.is_race_ready,
            "is_extra_verified": user.is_extra_verified,
            "in_zwiftpower": zp is not None,
            "zp_category": zp_cat,
            "zp_category_w": zp_cat_w,
            "in_zwiftracing": zr is not None,
            "zr_category": zr_cat,
            "zr_rating": zr_rating,
            "zr_phenotype": zr_phenotype,
            "zr_age": zr_age,
            "max_races": resp.max_races if resp else None,
            "rest_days": resp.rest_days if resp else None,
        }
        enriched_responders.append(entry)
        user_by_id[user.pk] = entry

    # Aggregate user IDs keyed by UTC cell
    utc_cell_user_ids: dict[str, list[int]] = {}
    for response in responses:
        for cell in response.available_cells:
            key = f"{cell['date']}|{cell['time']}"
            utc_cell_user_ids.setdefault(key, []).append(response.user.pk)

    # Determine display timezone
    user_tz = getattr(request.user, "timezone", "") or ""
    display_tz = user_tz or grid.grid_timezone or "UTC"
    tz_is_default = not user_tz

    grid_data = convert_grid_to_local(grid.dates, grid.time_slots, grid.blocked_cells, display_tz)

    # Re-key user IDs from UTC → local
    cell_user_ids: dict[str, list[int]] = {}
    for utc_key, local_key in grid_data["reverse_map"].items():
        if utc_key in utc_cell_user_ids:
            cell_user_ids[local_key] = utc_cell_user_ids[utc_key]

    # Build display_dates with day names for header
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    display_dates = []
    for d_str in grid_data["display_dates"]:
        d = date.fromisoformat(d_str)
        display_dates.append({"date_str": d_str[5:], "day_name": day_names[d.weekday()], "full_date": d_str})

    # Build grid_rows for server-side rendering
    blocked_set = grid_data["display_blocked"]
    grid_rows = []
    for time_slot in grid_data["display_time_slots"]:
        cells = []
        for d_info in display_dates:
            key = f"{d_info['full_date']}|{time_slot}"
            is_blocked = key in blocked_set
            uids = cell_user_ids.get(key, [])
            count = len(uids)
            if total_responders > 0 and count > 0:
                ratio = count / total_responders
                opacity = f"{0.3 + ratio * 0.7:.2f}"
                dark_text = ratio > 0.6
            else:
                ratio = 0
                opacity = "0"
                dark_text = False
            cells.append({
                "is_blocked": is_blocked,
                "count": count,
                "opacity": opacity,
                "dark_text": dark_text,
                "users": [user_by_id[uid] for uid in uids if uid in user_by_id],
            })
        grid_rows.append({"time_slot": time_slot, "cells": cells})

    logfire.debug(
        "Availability results viewed",
        grid_id=str(grid.id),
        user_id=request.user.id,
        event_id=event_pk,
        squad_id=squad_pk,
        total_responders=total_responders,
        display_tz=display_tz,
    )
    return render(
        request,
        "events/availability_results.html",
        {
            "event": event,
            "squad": squad,
            "grid": grid,
            "display_dates": display_dates,
            "grid_rows": grid_rows,
            "total_responders": total_responders,
            "enriched_responders": enriched_responders,
            "display_timezone": display_tz,
            "tz_is_default": tz_is_default,
        },
    )


@require_GET
@login_required
@team_member_required()
def manage_roles_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Display consolidated Discord role management for all event signups.

    Shows a table of all signups with event role and per-squad role toggle buttons.
    Accessible to users with assign_roles permission or the event's head captain role.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.

    Returns:
        Rendered role management page.

    Raises:
        PermissionDenied: If user lacks permission.

    """
    event = get_object_or_404(Event, pk=event_pk)
    if not _can_manage_event_roles(request.user, event):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You need Assign Roles permission or the Head Captain role for this event.")
    signups = event.signups.select_related("user").filter(status=EventSignup.Status.REGISTERED)
    enriched_signups = _enrich_signups(signups, event=event)

    # Squads that have a Discord role configured
    role_squads = list(event.squads.exclude(team_discord_role=0).exclude(team_discord_role__isnull=True))

    # Squads that have a captain Discord role configured
    captain_role_squads = list(event.squads.exclude(discord_captain_role=0).exclude(discord_captain_role__isnull=True))

    # Resolve Discord role IDs to names
    from apps.team.models import DiscordRole

    role_ids = set()
    if event.event_role:
        role_ids.add(str(event.event_role))
    for s in role_squads:
        role_ids.add(str(s.team_discord_role))
    for s in captain_role_squads:
        role_ids.add(str(s.discord_captain_role))
    role_names = dict(
        DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")
    ) if role_ids else {}

    # Build role info list for display
    role_info = []
    if event.event_role:
        role_info.append({
            "label": "Event Role",
            "name": role_names.get(str(event.event_role), ""),
            "role_id": event.event_role,
        })
    for s in role_squads:
        s.role_name = role_names.get(str(s.team_discord_role), "")
        role_info.append({
            "label": s.name,
            "name": s.role_name,
            "role_id": s.team_discord_role,
        })
    for s in captain_role_squads:
        s.captain_role_name = role_names.get(str(s.discord_captain_role), "")
        role_info.append({
            "label": f"{s.name} Captain",
            "name": s.captain_role_name,
            "role_id": s.discord_captain_role,
        })

    # Build squad membership lookup: {user_id: set(squad_ids)}
    squad_member_map: dict[int, set[int]] = {}
    for sm in SquadMember.objects.filter(squad__event=event, status=SquadMember.Status.MEMBER):
        squad_member_map.setdefault(sm.user_id, set()).add(sm.squad_id)

    # Enrich each signup with per-squad role status
    for entry in enriched_signups:
        user = entry["user"]
        user_squads = squad_member_map.get(user.pk, set())
        squad_role_status = []
        for squad in role_squads:
            if squad.pk in user_squads:
                squad_role_status.append({
                    "squad": squad,
                    "is_member": True,
                    "has_role": user.has_discord_role(squad.team_discord_role),
                })
            else:
                squad_role_status.append({
                    "squad": squad,
                    "is_member": False,
                    "has_role": False,
                })
        entry["squad_role_status"] = squad_role_status
        captain_role_status = []
        for squad in captain_role_squads:
            if squad.pk in user_squads:
                captain_role_status.append({
                    "squad": squad,
                    "is_member": True,
                    "has_role": user.has_discord_role(squad.discord_captain_role),
                })
            else:
                captain_role_status.append({
                    "squad": squad,
                    "is_member": False,
                    "has_role": False,
                })
        entry["captain_role_status"] = captain_role_status

    logfire.info(
        "Manage roles page viewed",
        event_id=event_pk,
        user_id=request.user.id,
        signup_count=len(enriched_signups),
    )
    return render(
        request,
        "events/manage_roles.html",
        {
            "event": event,
            "enriched_signups": enriched_signups,
            "role_squads": role_squads,
            "captain_role_squads": captain_role_squads,
            "role_info": role_info,
        },
    )


@login_required
@team_member_required()
@require_POST
def event_toggle_role_view(request: HttpRequest, event_pk: int, user_id: int) -> HttpResponse:
    """Toggle an event's Discord role for a signup user.

    Adds the role if the user doesn't have it, removes it if they do.
    Accessible to users with assign_roles permission or the event's head captain role.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        user_id: The target user primary key.

    Returns:
        HTMX partial or redirect to manage roles page.

    Raises:
        PermissionDenied: If user lacks permission.

    """
    event = get_object_or_404(Event, pk=event_pk)
    if not _can_manage_event_roles(request.user, event):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You need Assign Roles permission or the Head Captain role for this event.")
    role_id = event.event_role
    if not role_id:
        messages.error(request, "This event has no Discord role configured.")
        return redirect("events:manage_roles", event_pk=event_pk)

    target_user = get_object_or_404(User, pk=user_id)
    if not target_user.discord_id:
        messages.error(request, f"{target_user} has no linked Discord account.")
        return redirect("events:manage_roles", event_pk=event_pk)

    role_id_str = str(role_id)
    if target_user.has_discord_role(role_id):
        success = remove_discord_role(target_user.discord_id, role_id_str)
        if success:
            roles = dict(target_user.discord_roles or {})
            roles.pop(role_id_str, None)
            target_user.discord_roles = roles
            target_user.save(update_fields=["discord_roles"])
            logfire.info(
                "Event Discord role removed",
                event_id=event_pk,
                target_user_id=user_id,
                role_id=role_id_str,
                admin_user_id=request.user.id,
            )
            messages.success(request, f"Removed event role from {target_user}.")
        else:
            logfire.error(
                "Failed to remove event Discord role",
                event_id=event_pk,
                target_user_id=user_id,
                role_id=role_id_str,
            )
            messages.error(request, "Failed to remove Discord role. Check bot token configuration.")
    else:
        success = add_discord_role(target_user.discord_id, role_id_str)
        if success:
            roles = dict(target_user.discord_roles or {})
            roles[role_id_str] = event.title
            target_user.discord_roles = roles
            target_user.save(update_fields=["discord_roles"])
            logfire.info(
                "Event Discord role added",
                event_id=event_pk,
                target_user_id=user_id,
                role_id=role_id_str,
                admin_user_id=request.user.id,
            )
            messages.success(request, f"Added event role to {target_user}.")
        else:
            logfire.error(
                "Failed to add event Discord role",
                event_id=event_pk,
                target_user_id=user_id,
                role_id=role_id_str,
            )
            messages.error(request, "Failed to add Discord role. Check bot token configuration.")

    if request.headers.get("HX-Request"):
        has_role = target_user.has_discord_role(role_id)
        response = render(
            request,
            "events/_event_role_cell.html",
            {
                "event_pk": event_pk,
                "member_user_pk": user_id,
                "has_role": has_role,
            },
        )
        stored = messages.get_messages(request)
        msg_list = [{"message": str(m), "tags": m.tags} for m in stored]
        if msg_list:
            response["HX-Trigger"] = json.dumps({"showToast": msg_list})
        return response

    return redirect("events:manage_roles", event_pk=event_pk)


@team_member_required(raise_exception=True)
@require_http_methods(["GET", "POST"])
def squad_invite_view(request: HttpRequest, token: str) -> HttpResponse:
    """Show squad invite confirmation page (GET) or accept the invite (POST).

    Looks up squad by invite_token. On GET, shows event/squad info and current members.
    On POST, creates event signup if needed and adds user as a full squad member.

    Args:
        request: The HTTP request.
        token: The squad invite UUID token.

    Returns:
        Rendered confirmation page (GET) or redirect to my events (POST).

    """
    squad = get_object_or_404(Squad, invite_token=token)
    event = squad.event

    if not event.visible:
        logfire.warning(
            "Squad invite rejected: event not visible",
            squad_id=squad.pk,
            event_id=event.pk,
            user_id=request.user.id,
        )
        messages.error(request, "This event is not currently available.")
        return redirect("events:event_list")

    # Check if user is already a member
    already_member = SquadMember.objects.filter(
        squad=squad, user=request.user, status=SquadMember.Status.MEMBER
    ).exists()

    if request.method == "GET":
        members = (
            squad.squad_members.filter(status=SquadMember.Status.MEMBER)
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
        )
        return render(
            request,
            "events/squad_invite.html",
            {
                "squad": squad,
                "event": event,
                "members": members,
                "already_member": already_member,
                "token": token,
            },
        )

    # POST — accept the invite
    if already_member:
        messages.info(request, f"You're already a member of squad {squad.name}.")
        return redirect("events:my_events")

    # Ensure event signup exists and is active
    signup, created = EventSignup.objects.get_or_create(
        event=event,
        user=request.user,
        defaults={"status": EventSignup.Status.REGISTERED},
    )
    if not created and signup.status == EventSignup.Status.WITHDRAWN:
        signup.status = EventSignup.Status.REGISTERED
        signup.save(update_fields=["status"])

    # Add or upgrade squad membership
    _sm, sm_created = SquadMember.objects.update_or_create(
        squad=squad,
        user=request.user,
        defaults={"status": SquadMember.Status.MEMBER},
    )

    if sm_created:
        logfire.info(
            "User joined squad via invite link",
            squad_id=squad.pk,
            event_id=event.pk,
            user_id=request.user.id,
        )
        messages.success(request, f"You've been added to squad {squad.name}!")
    else:
        logfire.info(
            "User used invite link but already in squad",
            squad_id=squad.pk,
            event_id=event.pk,
            user_id=request.user.id,
        )
        messages.info(request, f"You're already a member of squad {squad.name}.")

    return redirect("events:my_events")


@discord_permission_required("event_admin", raise_exception=True)
@require_POST
def squad_regenerate_token_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Generate or regenerate an invite token for a squad.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.
        squad_pk: The squad primary key.

    Returns:
        Redirect to squad manage page.

    """
    squad = get_object_or_404(Squad, pk=squad_pk, event_id=event_pk)
    squad.regenerate_invite_token()
    logfire.info(
        "Squad invite token regenerated",
        squad_id=squad.pk,
        event_id=event_pk,
        admin_user_id=request.user.id,
    )
    messages.success(request, f"Invite link for {squad.name} has been generated.")
    return redirect("events:squad_manage", event_pk=event_pk)
