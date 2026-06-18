"""Views for events app."""

import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, available_timezones

import logfire
from constance import config
from datastar_py.django import DatastarResponse, ServerSentEventGenerator
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import discord_permission_required, team_member_required
from apps.accounts.discord_service import (
    add_discord_role,
    archive_discord_thread,
    create_discord_thread,
    delete_discord_thread,
    remove_discord_role,
    rename_discord_thread,
    send_discord_channel_message,
    sync_user_discord_roles,
)
from apps.accounts.models import Permissions, User
from apps.events import ds_service
from apps.events.calendar_utils import build_race_ics, race_calendar_urls, unsign_race_token
from apps.events.forms import EventForm, EventRoleSetupForm, SquadForm
from apps.events.models import (
    ZR_CATEGORY_ORDER,
    AvailabilityGrid,
    AvailabilityGridTemplate,
    AvailabilityResponse,
    AvailabilitySlotSelection,
    Event,
    EventSignup,
    SlotDS,
    Squad,
    SquadMember,
)
from apps.events.tz_utils import (
    TIMEZONE_CHOICES,
    convert_blocked_cells_to_utc,
    convert_grid_to_local,
    convert_local_to_utc,
    convert_utc_to_local_config,
    drop_fully_blocked_days,
)
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider

CATEGORY_COLUMNS = ["A+", "A", "B", "C", "D", "E"]


def _can_manage_event_squads(user: User, event: Event) -> bool:
    """Check if a user can create, edit, or delete squads for an event.

    Allowed for:
    - Superusers
    - Users with the ``event_admin`` permission
    - Users holding the event's head captain Discord role

    Args:
        user: The requesting user.
        event: The event to check against.

    Returns:
        True if the user can manage squads for this event.

    """
    if user.is_event_admin or user.is_superuser:
        return True
    return bool(event.head_captain_role_id and user.has_discord_role(event.head_captain_role_id))


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


def _can_manage_squad_availability(user: User, squad: Squad) -> bool:
    """Check if a user can manage availability for a specific squad.

    Allowed for:
    - Superusers
    - Users with the ``event_admin`` permission
    - The squad's captain or vice-captain
    - Users holding the squad's Discord captain role
    - Users holding the parent event's head captain Discord role

    Args:
        user: The requesting user.
        squad: The squad whose availability is being managed.

    Returns:
        True if the user can manage availability for this squad.

    """
    if user.is_event_admin or user.is_superuser:
        return True
    if squad.captains.filter(pk=user.pk).exists() or squad.vice_captains.filter(pk=user.pk).exists():
        return True
    if squad.discord_captain_role and user.has_discord_role(squad.discord_captain_role):
        return True
    event = squad.event
    return bool(event.head_captain_role_id and user.has_discord_role(event.head_captain_role_id))


def _can_view_v_report(user: User, event: Event) -> bool:
    """Check if a user can view the V Report for an event.

    Allowed for event admins, superusers, squad captains/vice-captains for any squad
    in the event, and holders of the event's head captain Discord role.

    Args:
        user: The requesting user.
        event: The event to check against.

    Returns:
        True if the user can view the V Report.

    """
    if user.is_event_admin or user.is_superuser:
        return True
    if event.head_captain_role_id and user.has_discord_role(event.head_captain_role_id):
        return True
    return event.squads.filter(Q(captains=user) | Q(vice_captains=user)).exists()


def _assign_discord_role(user, role_id: int, role_display_name: str, *, admin_user_id: int) -> bool | None:
    """Add a Discord role to a user, updating their local discord_roles cache.

    Args:
        user: The User to assign the role to.
        role_id: The Discord role ID to add.
        role_display_name: Display name for logging.
        admin_user_id: The admin performing the action.

    Returns:
        None if skipped (no discord_id or role_id is 0), True if success/already has, False on API failure.

    """
    if not role_id or not user.discord_id:
        return None
    role_id_str = str(role_id)
    if user.has_discord_role(role_id_str):
        return True
    success = add_discord_role(user.discord_id, role_id_str)
    if success:
        roles = user.discord_roles or {}
        roles[role_id_str] = role_display_name
        user.discord_roles = roles
        user.save(update_fields=["discord_roles"])
        logfire.info(
            "Auto-assigned Discord role",
            user_id=user.id,
            role_id=role_id_str,
            role_name=role_display_name,
            admin_user_id=admin_user_id,
        )
    else:
        logfire.error(
            "Failed to auto-assign Discord role",
            user_id=user.id,
            role_id=role_id_str,
            role_name=role_display_name,
            admin_user_id=admin_user_id,
        )
    return success


def _unassign_discord_role(user, role_id: int, *, admin_user_id: int) -> bool | None:
    """Remove a Discord role from a user, updating their local discord_roles cache.

    Args:
        user: The User to remove the role from.
        role_id: The Discord role ID to remove.
        admin_user_id: The admin performing the action.

    Returns:
        None if skipped (no discord_id or role_id is 0), True if success/already missing, False on API failure.

    """
    if not role_id or not user.discord_id:
        return None
    role_id_str = str(role_id)
    if not user.has_discord_role(role_id_str):
        return True
    success = remove_discord_role(user.discord_id, role_id_str)
    if success:
        roles = user.discord_roles or {}
        roles.pop(role_id_str, None)
        user.discord_roles = roles
        user.save(update_fields=["discord_roles"])
        logfire.info("Auto-unassigned Discord role", user_id=user.id, role_id=role_id_str, admin_user_id=admin_user_id)
    else:
        logfire.error(
            "Failed to auto-unassign Discord role", user_id=user.id, role_id=role_id_str, admin_user_id=admin_user_id
        )
    return success


def _build_role_badges(user, event):
    """Build role badge dicts for a single user in the context of an event.

    Args:
        user: The User instance (must have up-to-date discord_roles).
        event: The Event instance.

    Returns:
        List of dicts with 'name' and 'has_role' keys.

    """
    badges = []
    if event.event_role:
        badges.append({"name": "Event", "has_role": user.has_discord_role(event.event_role)})

    user_squads = list(
        Squad.objects.filter(
            pk__in=SquadMember.objects.filter(squad__event=event, user=user).values_list("squad_id", flat=True),
        ).prefetch_related("captains", "vice_captains")
    )
    for sq in user_squads:
        if sq.team_discord_role:
            badges.append({"name": sq.name, "has_role": user.has_discord_role(sq.team_discord_role)})
        if sq.discord_captain_role and sq.is_leader(user):
            badges.append({"name": f"{sq.name} Cpt", "has_role": user.has_discord_role(sq.discord_captain_role)})
    return badges


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
        .prefetch_related("squad__captains", "squad__vice_captains")
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
        captain_role_id = sm.squad.discord_captain_role
        is_captain_or_vc = sm.squad.is_leader(user)
        has_captain_role = user.has_discord_role(captain_role_id) if captain_role_id and is_captain_or_vc else None

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
            "has_captain_role": has_captain_role,
        }
        by_squad.setdefault(sm.squad_id, []).append(entry)

    return by_squad


def _build_manage_squad_context(request, event, squad):
    """Build template context for the self-contained roster panel on the manage-squads page.

    Computes enriched members (with signup ids for remove forms), the list of signups
    available to add to this squad (all event signups not already members), and Discord
    role names for display.

    Args:
        request: The HTTP request (used to resolve the manage permission).
        event: The parent Event.
        squad: The Squad whose panel is being rendered.

    Returns:
        Context dict for ``events/_squad_manage_panel.html``.

    """
    from apps.team.models import DiscordRole

    squad_members_data = _enrich_squad_members(event)
    members = squad_members_data.get(squad.pk, [])
    all_signups = list(EventSignup.objects.filter(event=event).select_related("user"))
    signup_by_user = {s.user_id: s.pk for s in all_signups}
    for member in members:
        member["signup_id"] = signup_by_user.get(member["user"].pk)

    member_user_ids = {m["user"].pk for m in members}
    available_signups = sorted(
        (s for s in all_signups if s.user_id not in member_user_ids),
        key=lambda s: (s.user.get_full_name() or s.user.discord_username or "").lower(),
    )

    role_ids = {str(squad.team_discord_role)} if squad.team_discord_role else set()
    if event.event_role:
        role_ids.add(str(event.event_role))
    role_names = (
        dict(DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")) if role_ids else {}
    )
    squad.role_name = role_names.get(str(squad.team_discord_role), "") if squad.team_discord_role else ""

    return {
        "squad": squad,
        "members": members,
        "available_signups": available_signups,
        "event_role_name": role_names.get(str(event.event_role), "") if event.event_role else "",
        "event_pk": event.pk,
        "can_manage": _can_manage_event_squads(request.user, event),
        "oob": False,
        "htmx_count_oob": True,
    }


def _render_manage_squad_panel(request, event, squad_pk):
    """Render the manage-squads roster panel for a single squad as an HTMX response.

    Args:
        request: The HTTP request.
        event: The parent Event.
        squad_pk: Primary key of the squad to re-render.

    Returns:
        An ``HttpResponse`` containing the rendered panel.

    """
    squad = get_object_or_404(
        Squad.objects.prefetch_related("captains", "vice_captains"), pk=squad_pk, event=event
    )
    html = render_to_string(
        "events/_squad_manage_panel.html",
        _build_manage_squad_context(request, event, squad),
        request=request,
    )
    return HttpResponse(html)


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

    # Build captain/VC lookup: user_id -> set of squad PKs where they are captain/VC
    captain_squads: dict[int, set] = {}
    if event:
        for sq in event.squads.prefetch_related("captains", "vice_captains").all():
            for leader in (*sq.captains.all(), *sq.vice_captains.all()):
                captain_squads.setdefault(leader.pk, set()).add(sq.pk)

    enriched = []
    for signup in signups:
        user = signup.user
        zwid = user.zwid
        zp = zp_by_zwid.get(zwid)
        zr = zr_by_zwid.get(zwid)

        zp_ftp = zp.ftp if zp else None
        zp_weight = zp.weight if zp else None
        wkg = round(Decimal(zp_ftp) / zp_weight, 2) if zp_ftp and zp_weight and zp_weight > 0 else None

        # Build role badges
        role_badges = []
        if event_role_id:
            role_badges.append({"name": "Event", "has_role": user.has_discord_role(event_role_id)})
        user_squads = squads_by_user.get(user.pk, [])
        user_captain_squad_pks = captain_squads.get(user.pk, set())
        for sq in user_squads:
            if sq.team_discord_role:
                role_badges.append({"name": sq.name, "has_role": user.has_discord_role(sq.team_discord_role)})
            if sq.discord_captain_role and sq.pk in user_captain_squad_pks:
                role_badges.append({
                    "name": f"{sq.name} Cpt",
                    "has_role": user.has_discord_role(sq.discord_captain_role),
                })

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
            "assigned_squads": user_squads,
            "role_badges": role_badges,
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
    show_past = request.GET.get("show_past") == "on"
    signups = (
        EventSignup.objects
        .filter(user=request.user, status=EventSignup.Status.REGISTERED)
        .select_related("event")
        .order_by("-event__start_date")
    )
    if not show_past:
        signups = signups.filter(event__end_date__gte=timezone.now().date())
    squad_memberships = (
        SquadMember.objects.filter(user=request.user, status=SquadMember.Status.MEMBER)
        .select_related("squad", "squad__event")
        .prefetch_related("squad__captains", "squad__vice_captains")
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

    user_tz = getattr(request.user, "timezone", "") or ""

    user_selections = []
    if all_squad_ids:
        user_selections = list(
            AvailabilitySlotSelection.objects
            .filter(selected_users=request.user, grid__squad_id__in=all_squad_ids)
            .select_related("grid", "grid__squad")
            .prefetch_related("selected_users")
            .order_by("slot_date", "slot_time")
        )

    all_sms: list = []
    if all_squad_ids:
        all_sms = list(
            SquadMember.objects
            .filter(squad_id__in=all_squad_ids, status=SquadMember.Status.MEMBER)
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
        )

    users_for_lookup: dict[int, User] = {sm.user.pk: sm.user for sm in all_sms}
    for sel in user_selections:
        for u in sel.selected_users.all():
            users_for_lookup.setdefault(u.pk, u)

    zwids = [u.zwid for u in users_for_lookup.values() if u.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}

    def _enrich(user: User) -> dict:
        zwid = user.zwid
        zp = zp_by_zwid.get(zwid) if zwid else None
        zr = zr_by_zwid.get(zwid) if zwid else None
        return {
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
        }

    enriched_by_user_id = {pk: _enrich(u) for pk, u in users_for_lookup.items()}

    members_by_squad: dict[int, list] = {}
    for sm in all_sms:
        members_by_squad.setdefault(sm.squad_id, []).append(enriched_by_user_id[sm.user.pk])

    # Slot selections that include the requesting user, grouped by squad
    slot_selections_by_squad: dict[int, list] = {}
    now_utc = timezone.now()
    today_local = now_utc.astimezone(ZoneInfo(user_tz) if user_tz else now_utc.tzinfo).date()
    for sel in user_selections:
        display_tz = user_tz or sel.grid.grid_timezone or "UTC"
        try:
            tz_obj = ZoneInfo(display_tz)
        except Exception:
            tz_obj = ZoneInfo("UTC")
            display_tz = "UTC"
        utc_dt = datetime.combine(
            sel.slot_date,
            datetime.strptime(sel.slot_time, "%H:%M").time(),  # noqa: DTZ007  # clock-only parse, no date used
            tzinfo=ZoneInfo("UTC"),
        )
        local_dt = utc_dt.astimezone(tz_obj)
        days_until = (local_dt.date() - today_local).days
        if days_until < 0 and not show_past:
            continue
        if days_until > 1:
            days_label = f"in {days_until} days"
            days_class = "badge-info"
        elif days_until == 1:
            days_label = "tomorrow"
            days_class = "badge-warning"
        elif days_until == 0:
            days_label = "today"
            days_class = "badge-warning"
        else:
            days_label = f"{abs(days_until)} day{'s' if abs(days_until) != 1 else ''} ago"
            days_class = "badge-ghost"
        riders = [
            enriched_by_user_id[u.pk]
            for u in sel.selected_users.all()
            if u.pk in enriched_by_user_id
        ]
        if sel.status == AvailabilitySlotSelection.Status.CONFIRMED:
            status_class = "badge-success"
        elif sel.status == AvailabilitySlotSelection.Status.PENDING:
            status_class = "badge-warning"
        else:
            status_class = ""
        slot_selections_by_squad.setdefault(sel.grid.squad_id, []).append({
            "selection": sel,
            "grid_title": sel.grid.title or "Availability Grid",
            "local_day": local_dt.strftime("%a"),
            "local_date": local_dt.strftime("%b %-d"),
            "local_time": local_dt.strftime("%H:%M"),
            "display_timezone": display_tz,
            "days_until": days_until,
            "days_label": days_label,
            "days_class": days_class,
            "status_label": sel.get_status_display(),
            "status_class": status_class,
            "is_status_visible": sel.status != AvailabilitySlotSelection.Status.NONE,
            "riders": riders,
            **race_calendar_urls(sel, request),
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
                "user_slot_selections": slot_selections_by_squad.get(squad.pk, []),
                "can_manage_availability": _can_manage_squad_availability(request.user, squad),
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
            "show_past": show_past,
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
    show_past = request.GET.get("show_past") == "on"
    if search_query:
        events = events.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))
    if not show_past:
        events = events.filter(end_date__gte=timezone.now().date())
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
            "show_past": show_past,
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
        event.squads.prefetch_related("captains", "vice_captains").annotate(member_count=Count("squad_members")).all()
    )
    signups = event.signups.select_related("user").all()
    user_signup = event.signups.filter(user=request.user).first()
    # Event admins always see the full signup table; with show_signups on, any
    # team member can expand a names-only list (gated further in the template).
    can_view_signups = request.user.is_event_admin or event.show_signups
    enriched_signups = _enrich_signups(signups, event=event) if can_view_signups else []

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
            "can_view_signups": can_view_signups,
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
            "can_manage_squads": _can_manage_event_squads(request.user, event),
            "can_add_members": _can_add_members(request.user, event),
            "can_view_v_report": _can_view_v_report(request.user, event),
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

    enriched_signups = _enrich_signups(event.signups.select_related("user").all(), event=event)

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

    # Resolve coordinator role IDs to names for the read-only display.
    coord_ids = [str(rid) for rid in (event.coordinator_role_ids or [])]
    if coord_ids:
        coord_names_by_id = dict(
            DiscordRole.objects.filter(role_id__in=coord_ids).values_list("role_id", "name")
        )
        role_display["coordinator_names"] = [
            coord_names_by_id.get(rid, f"Unknown ({rid})") for rid in coord_ids
        ]
    else:
        role_display["coordinator_names"] = []

    try:
        allowed_prefixes = json.loads(config.EVENT_ROLE_PREFIXES)
    except json.JSONDecodeError, ValueError, TypeError:
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

    if not _can_manage_event_squads(request.user, event):
        logfire.warning(
            "Unauthorized squad manage attempt",
            event_id=event_pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    squads = list(
        event.squads.prefetch_related("captains", "vice_captains")
        .annotate(member_count=Count("squad_members"))
        .order_by("name")
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

    if event.event_role:
        role_ids.add(str(event.event_role))

    from apps.team.models import DiscordChannel, DiscordRole

    channel_names = (
        dict(DiscordChannel.objects.filter(channel_id__in=channel_ids).values_list("channel_id", "name"))
        if channel_ids
        else {}
    )
    role_names = (
        dict(DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")) if role_ids else {}
    )
    event_role_name = role_names.get(str(event.event_role), "") if event.event_role else ""

    # Attach availability grids (published/closed) grouped by squad
    grids_by_squad: dict[int, list] = {}
    for grid in AvailabilityGrid.objects.filter(
        squad__event=event,
        status__in=[AvailabilityGrid.Status.PUBLISHED, AvailabilityGrid.Status.CLOSED],
    ):
        grids_by_squad.setdefault(grid.squad_id, []).append(grid)

    # Enriched members + add-rider options for the expandable Riders section
    squad_members_data = _enrich_squad_members(event)
    all_signups = list(EventSignup.objects.filter(event=event).select_related("user"))
    signup_by_user = {s.user_id: s.pk for s in all_signups}

    for s in squads:
        s.channel_name = channel_names.get(str(s.discord_channel_id), "") if s.discord_channel_id else ""
        s.audio_name = channel_names.get(str(s.audio_channel_id), "") if s.audio_channel_id else ""
        s.role_name = role_names.get(str(s.team_discord_role), "") if s.team_discord_role else ""
        s.active_grids = grids_by_squad.get(s.pk, [])

        members = squad_members_data.get(s.pk, [])
        for member in members:
            member["signup_id"] = signup_by_user.get(member["user"].pk)
        s.enriched_members = members
        member_user_ids = {m["user"].pk for m in members}
        s.available_signups = sorted(
            (su for su in all_signups if su.user_id not in member_user_ids),
            key=lambda su: (su.user.get_full_name() or su.user.discord_username or "").lower(),
        )

    return render(
        request,
        "events/squad_manage.html",
        {
            "event": event,
            "squads": squads,
            "event_role_name": event_role_name,
            "can_manage": True,
            "can_manage_roles": _can_manage_event_roles(request.user, event),
        },
    )


@login_required
@team_member_required()
@require_GET
def event_all_races_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Paginated list of every scheduled race in the event from today forward.

    25 races per page, ordered chronologically. ``?page=N`` selects a page
    (out-of-range falls back to the last available page).

    Args:
        request: The HTTP request.
        event_pk: The event primary key.

    Returns:
        Rendered all-races page.

    """
    event = get_object_or_404(Event, pk=event_pk)

    user_tz = getattr(request.user, "timezone", "") or ""
    now_utc = timezone.now()
    today_local = now_utc.astimezone(ZoneInfo(user_tz) if user_tz else now_utc.tzinfo).date()

    all_selections = (
        AvailabilitySlotSelection.objects
        .filter(grid__squad__event=event, slot_date__gte=today_local)
        .select_related("grid", "grid__squad")
        .prefetch_related("selected_users", "grid__squad__captains", "grid__squad__vice_captains")
        .order_by("slot_date", "slot_time", "grid__squad__name")
    )

    paginator = Paginator(all_selections, 25)
    page_number = request.GET.get("page") or 1
    try:
        page_obj = paginator.page(page_number)
    except Exception:
        page_obj = paginator.page(paginator.num_pages or 1)
    selections = list(page_obj.object_list)

    # Collect every selected user across the visible window for one zp/zr lookup
    users_for_lookup: dict[int, User] = {}
    for sel in selections:
        for u in sel.selected_users.all():
            users_for_lookup.setdefault(u.pk, u)

    zwids = [u.zwid for u in users_for_lookup.values() if u.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}

    def _enrich(user: User) -> dict:
        zwid = user.zwid
        zp = zp_by_zwid.get(zwid) if zwid else None
        zr = zr_by_zwid.get(zwid) if zwid else None
        return {
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
        }

    enriched_by_user_id = {pk: _enrich(u) for pk, u in users_for_lookup.items()}

    slots: list[dict] = []
    for sel in selections:
        display_tz = user_tz or sel.grid.grid_timezone or "UTC"
        try:
            tz_obj = ZoneInfo(display_tz)
        except Exception:
            tz_obj = ZoneInfo("UTC")
            display_tz = "UTC"
        utc_dt = datetime.combine(
            sel.slot_date,
            datetime.strptime(sel.slot_time, "%H:%M").time(),  # noqa: DTZ007  # clock-only parse, no date used
            tzinfo=ZoneInfo("UTC"),
        )
        local_dt = utc_dt.astimezone(tz_obj)
        days_until = (local_dt.date() - today_local).days
        if days_until > 1:
            days_label = f"in {days_until} days"
            days_class = "badge-info"
        elif days_until == 1:
            days_label = "tomorrow"
            days_class = "badge-warning"
        elif days_until == 0:
            days_label = "today"
            days_class = "badge-warning"
        else:
            days_label = f"{abs(days_until)} day{'s' if abs(days_until) != 1 else ''} ago"
            days_class = "badge-ghost"
        if sel.status == AvailabilitySlotSelection.Status.CONFIRMED:
            status_class = "badge-success"
        elif sel.status == AvailabilitySlotSelection.Status.PENDING:
            status_class = "badge-warning"
        else:
            status_class = ""
        riders = [
            enriched_by_user_id[u.pk]
            for u in sel.selected_users.all()
            if u.pk in enriched_by_user_id
        ]
        slots.append({
            "selection": sel,
            "squad": sel.grid.squad,
            "squad_name": sel.grid.squad.name,
            "grid_title": sel.grid.title or "Availability Grid",
            "local_day": local_dt.strftime("%a"),
            "local_date": local_dt.strftime("%b %-d"),
            "local_time": local_dt.strftime("%H:%M"),
            "display_timezone": display_tz,
            "days_until": days_until,
            "days_label": days_label,
            "days_class": days_class,
            "status_label": sel.get_status_display(),
            "status_class": status_class,
            "is_status_visible": sel.status != AvailabilitySlotSelection.Status.NONE,
            "riders": riders,
        })

    logfire.debug(
        "Event all races viewed",
        event_id=event_pk,
        user_id=request.user.id,
        page=page_obj.number,
        total_pages=paginator.num_pages,
        slot_count=len(slots),
        total_count=paginator.count,
    )

    return render(
        request,
        "events/event_all_races.html",
        {
            "event": event,
            "slots": slots,
            "page_obj": page_obj,
            "paginator": paginator,
            "today": today_local,
            "guild_id": config.GUILD_ID,
        },
    )


@login_required
@team_member_required()
@require_GET
def squad_v_report_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Display verification report for all registered signups.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.

    Returns:
        Rendered V Report page with per-rider verification day counts.

    """
    event = get_object_or_404(Event, pk=event_pk)

    if not _can_view_v_report(request.user, event):
        logfire.warning(
            "Unauthorized squad v-report attempt",
            event_id=event_pk,
            user_id=request.user.id,
            username=request.user.username,
        )
        messages.error(request, "You don't have permission to view Eligibility.")
        return redirect("events:event_detail", pk=event_pk)

    signups = event.signups.filter(status=EventSignup.Status.REGISTERED).select_related("user")
    enriched = _enrich_signups(signups, event=event)

    from apps.team.services import verification_days_bulk

    verification_by_user = verification_days_bulk([entry["user"] for entry in enriched])
    for entry in enriched:
        entry["verification"] = verification_by_user[entry["user"].id]

    enriched.sort(
        key=lambda e: (
            e["verification"]["race_ready_days"] is None,
            e["verification"]["race_ready_days"] if e["verification"]["race_ready_days"] is not None else 0,
            (e["user"].get_full_name() or e["user"].discord_username or "").lower(),
        ),
    )

    # Filter: riders whose eligibility (race-ready) expiration falls within N days.
    expiring_raw = request.GET.get("expiring", "").strip()
    expiring_days = int(expiring_raw) if expiring_raw.isdigit() else None
    if expiring_days is not None:
        enriched = [
            e
            for e in enriched
            if e["verification"]["race_ready_days"] is not None
            and e["verification"]["race_ready_days"] <= expiring_days
        ]

    # Grouping: optionally group riders by squad (a rider in multiple squads appears
    # in each), with an "Unassigned" group for riders in no squad.
    group_by = request.GET.get("group", "")
    groups = None
    if group_by == "squad":
        member_ids_by_squad: dict[int, set[int]] = {}
        assigned: set[int] = set()
        for squad_id, user_id in SquadMember.objects.filter(
            squad__event=event, status=SquadMember.Status.MEMBER
        ).values_list("squad_id", "user_id"):
            member_ids_by_squad.setdefault(squad_id, set()).add(user_id)
            assigned.add(user_id)

        groups = []
        for squad in event.squads.order_by("name"):
            ids = member_ids_by_squad.get(squad.id, set())
            rows = [e for e in enriched if e["user"].id in ids]
            if rows:
                groups.append({"name": squad.name, "rows": rows})
        unassigned = [e for e in enriched if e["user"].id not in assigned]
        if unassigned:
            groups.append({"name": "Unassigned", "rows": unassigned})

    # Squads tab: squad members whose current category/gender now violates their
    # squad's enforced limits (e.g. a rider upgraded after being assigned).
    members_by_squad = _enrich_squad_members(event)
    squad_violations = []
    for squad in event.squads.order_by("name"):
        rows = []
        for member in members_by_squad.get(squad.id, []):
            checks = (
                squad.check_zwift_eligibility(member["zp_category"]),
                squad.check_womens_zwift_eligibility(member["zp_category_w"]),
                squad.check_zr_eligibility(member["zr_category"]),
                squad.check_gender_eligibility(member["gender"]),
            )
            reasons = [reason for ok, reason in checks if not ok]
            if reasons:
                rows.append({"member": member, "reasons": reasons})
        if rows:
            squad_violations.append({"name": squad.name, "rows": rows})

    logfire.info(
        "Squad v-report viewed",
        event_id=event_pk,
        user_id=request.user.id,
        username=request.user.username,
        rider_count=len(enriched),
        group_by=group_by,
        expiring_days=expiring_days,
        squad_violation_count=sum(len(s["rows"]) for s in squad_violations),
    )

    return render(
        request,
        "events/squad_v_report.html",
        {
            "event": event,
            "signups": enriched,
            "groups": groups,
            "group_by": group_by,
            "expiring": expiring_raw if expiring_days is not None else "",
            "height_never_expires": config.HEIGHT_VERIFICATION_DAYS == 0,
            "squad_violations": squad_violations,
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

    signup_squad_gender = []
    if event.squad_gender_required and event.squad_gender_options:
        signup_squad_gender = request.POST.getlist("signup_squad_gender")
        if not signup_squad_gender:
            messages.error(request, "Please select at least one squad gender preference.")
            return redirect("events:event_detail", pk=pk)
        invalid = [g for g in signup_squad_gender if g not in event.squad_gender_options]
        if invalid:
            messages.error(request, "Invalid squad gender preference selection.")
            return redirect("events:event_detail", pk=pk)

    notes = request.POST.get("notes", "").strip()

    signup = EventSignup.objects.create(
        event=event,
        user=request.user,
        signup_timezone=signup_timezone,
        signup_squad_gender=signup_squad_gender,
        notes=notes,
    )
    logfire.info(
        "Event signup created",
        event_id=pk,
        event_title=event.title,
        user_id=request.user.id,
        signup_timezone=signup_timezone,
        signup_squad_gender=signup_squad_gender,
    )
    from apps.events.tasks import enqueue_signup_notification

    enqueue_signup_notification(signup, request=request)
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

    signup_squad_gender = []
    if event.squad_gender_required and event.squad_gender_options:
        signup_squad_gender = request.POST.getlist("signup_squad_gender")
        if not signup_squad_gender:
            messages.error(request, "Please select at least one squad gender preference.")
            return redirect("events:event_detail", pk=pk)
        invalid = [g for g in signup_squad_gender if g not in event.squad_gender_options]
        if invalid:
            messages.error(request, "Invalid squad gender preference selection.")
            return redirect("events:event_detail", pk=pk)

    signup.signup_timezone = signup_timezone
    signup.signup_squad_gender = signup_squad_gender
    signup.notes = request.POST.get("notes", "").strip()
    signup.save(update_fields=["signup_timezone", "signup_squad_gender", "notes", "updated_at"])
    logfire.info(
        "Event signup updated",
        event_id=pk,
        event_title=event.title,
        user_id=request.user.id,
        signup_timezone=signup_timezone,
        signup_squad_gender=signup_squad_gender,
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

    if not _can_manage_event_squads(request.user, event):
        logfire.warning(
            "Unauthorized squad creation attempt",
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    if request.method == "POST":
        form = SquadForm(
            request.POST,
            event_prefixes=event.prefixes or [],
        )
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
        form = SquadForm(event_prefixes=event.prefixes or [])

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

    if not _can_manage_event_squads(request.user, event):
        logfire.warning(
            "Unauthorized squad edit attempt",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage squads.")
        return redirect("events:event_detail", pk=event_pk)

    if request.method == "POST":
        form = SquadForm(
            request.POST,
            instance=squad,
            event_prefixes=event.prefixes or [],
        )
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
        form = SquadForm(
            instance=squad,
            event_prefixes=event.prefixes or [],
        )

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

    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized squad availability access attempt",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    availability_grids = squad.availability_grids.all()
    availability_templates = squad.availability_templates.all()

    return render(
        request,
        "events/squad_availability.html",
        {
            "event": event,
            "squad": squad,
            "availability_grids": availability_grids,
            "availability_templates": availability_templates,
            "today": timezone.now().date().isoformat(),
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

    if not _can_manage_event_squads(request.user, event):
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

    if not _can_manage_event_squads(request.user, event):
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
    is_htmx = bool(request.headers.get("HX-Request"))

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
                _unassign_discord_role(signup.user, remove_squad.team_discord_role, admin_user_id=request.user.id)
                # If user was captain/VC, drop them from those roles and remove the captain Discord role
                was_leader = (
                    remove_squad.captains.filter(pk=signup.user_id).exists()
                    or remove_squad.vice_captains.filter(pk=signup.user_id).exists()
                )
                if was_leader:
                    remove_squad.captains.remove(signup.user_id)
                    remove_squad.vice_captains.remove(signup.user_id)
                    _unassign_discord_role(
                        signup.user, remove_squad.discord_captain_role, admin_user_id=request.user.id
                    )
                if not is_htmx:
                    messages.success(request, f"{signup.user} removed from {remove_squad.name}.")
            elif not is_htmx:
                messages.info(request, f"{signup.user} was not in {remove_squad.name}.")
        else:
            # Collect squads before deleting so we can remove roles
            user_squad_members = list(
                SquadMember.objects.filter(squad__event=event, user=signup.user).select_related("squad")
            )
            deleted, _ = SquadMember.objects.filter(squad__event=event, user=signup.user).delete()
            if deleted:
                logfire.info(
                    "All squad assignments removed",
                    event_id=event_pk,
                    user_id=signup.user_id,
                    admin_user_id=request.user.id,
                )
                for sm in user_squad_members:
                    sq = sm.squad
                    _unassign_discord_role(signup.user, sq.team_discord_role, admin_user_id=request.user.id)
                    was_leader = (
                        sq.captains.filter(pk=signup.user_id).exists()
                        or sq.vice_captains.filter(pk=signup.user_id).exists()
                    )
                    if was_leader:
                        sq.captains.remove(signup.user_id)
                        sq.vice_captains.remove(signup.user_id)
                        _unassign_discord_role(signup.user, sq.discord_captain_role, admin_user_id=request.user.id)
                if not is_htmx:
                    messages.success(request, f"{signup.user} removed from all squads.")
            elif not is_htmx:
                messages.info(request, f"{signup.user} was not assigned to any squad.")
    else:
        squad = get_object_or_404(Squad, pk=squad_id, event=event)
        rider_zr = ""
        rider_zwift_cat = ""
        rider_womens_cat = ""
        if signup.user.zwid:
            zr = ZRRider.objects.filter(zwid=signup.user.zwid).first()
            rider_zr = getattr(zr, "race_current_category", "") or "" if zr else ""
            zp = ZPTeamRiders.objects.filter(zwid=signup.user.zwid).first()
            if zp:
                rider_zwift_cat = ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp.div else ""
                rider_womens_cat = ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp.divw else ""
        for ok, reason in (
            squad.check_gender_eligibility(signup.user.gender),
            squad.check_zwift_eligibility(rider_zwift_cat),
            squad.check_womens_zwift_eligibility(rider_womens_cat),
            squad.check_zr_eligibility(rider_zr),
        ):
            if ok:
                continue
            logfire.info(
                "Squad assignment blocked by squad requirements",
                event_id=event_pk,
                squad_id=squad.pk,
                squad_name=squad.name,
                user_id=signup.user_id,
                admin_user_id=request.user.id,
                rider_zr_category=rider_zr,
                rider_gender=signup.user.gender,
                reason=reason,
            )
            block_msg = f"Cannot add {signup.user} to {squad.name}: {reason}."
            if is_htmx:
                resp = HttpResponse(status=204)
                resp["HX-Trigger"] = json.dumps({"showToast": [{"message": block_msg, "tags": "error"}]})
                return resp
            messages.error(request, block_msg)
            return redirect("events:event_detail", pk=event_pk)
        SquadMember.objects.update_or_create(
            squad=squad,
            user=signup.user,
            defaults={"status": SquadMember.Status.MEMBER},
        )
        _assign_discord_role(signup.user, squad.team_discord_role, squad.name, admin_user_id=request.user.id)
        logfire.info(
            "Squad assignment created",
            event_id=event_pk,
            squad_id=squad.pk,
            squad_name=squad.name,
            user_id=signup.user_id,
            admin_user_id=request.user.id,
        )
        if not is_htmx:
            messages.success(request, f"{signup.user} assigned to {squad.name}.")

    # HTMX: return just the updated squad cell instead of full page reload
    if request.headers.get("HX-Request"):
        hx_url = request.headers.get("HX-Current-URL", "")
        # Manage-squads page: re-render just the affected squad's self-contained roster panel
        if "squads/manage" in hx_url:
            affected_pk = squad_id if squad_id != 0 else int(request.POST.get("remove_squad_id") or 0)
            if affected_pk:
                return _render_manage_squad_panel(request, event, affected_pk)

        assigned_squads = list(
            Squad.objects.filter(
                pk__in=SquadMember.objects.filter(squad__event=event, user=signup.user).values_list(
                    "squad_id", flat=True
                ),
            )
        )
        all_squads = list(event.squads.all())
        cell_html = render_to_string(
            "events/_squad_cell.html",
            {
                "assigned_squads": assigned_squads,
                "squads": all_squads,
                "event_pk": event_pk,
                "signup_id": signup.pk,
            },
            request=request,
        )

        # If request came from the assign-riders page, include OOB squad panel updates
        if "assign-riders" in hx_url:
            squad_members_data = _enrich_squad_members(event)
            # Map user_id -> signup pk for remove buttons in squad panels
            signup_by_user = dict(EventSignup.objects.filter(event=event).values_list("user_id", "pk"))
            for members in squad_members_data.values():
                for member in members:
                    member["signup_id"] = signup_by_user.get(member["user"].pk)

            # Resolve role names for squad panels
            from apps.team.models import DiscordRole

            role_ids = {str(sq.team_discord_role) for sq in all_squads if sq.team_discord_role}
            if event.event_role:
                role_ids.add(str(event.event_role))
            role_names = (
                dict(DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name"))
                if role_ids
                else {}
            )
            event_role_name = role_names.get(str(event.event_role), "") if event.event_role else ""

            is_event_admin = request.user.is_event_admin or request.user.is_superuser
            oob_html = ""
            for sq in all_squads:
                sq.role_name = role_names.get(str(sq.team_discord_role), "") if sq.team_discord_role else ""
                oob_html += render_to_string(
                    "events/_squad_panel.html",
                    {
                        "squad": sq,
                        "members": squad_members_data.get(sq.pk, []),
                        "event_role_name": event_role_name,
                        "event_pk": event_pk,
                        "oob": True,
                        "is_event_admin": is_event_admin,
                    },
                    request=request,
                )
            # OOB update for role badges of the affected user
            signup.user.refresh_from_db(fields=["discord_roles"])
            role_badges = _build_role_badges(signup.user, event)
            oob_html += render_to_string(
                "events/_role_badges.html",
                {"signup_id": signup.pk, "role_badges": role_badges, "oob": True},
                request=request,
            )

            return HttpResponse(cell_html + oob_html)

        return HttpResponse(cell_html)

    return redirect("events:event_detail", pk=event_pk)


@login_required
@team_member_required()
@require_POST
def squad_set_captain_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Set or unset a squad's captain or vice-captain.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        Updated squad panel HTML (HTMX) or redirect.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not _can_manage_event_squads(request.user, event):
        logfire.warning("Unauthorized squad captain set attempt", event_id=event_pk, user_id=request.user.id)
        messages.error(request, "You don't have permission to set squad captains.")
        return redirect("events:event_detail", pk=event_pk)

    user_id = request.POST.get("user_id")
    role = request.POST.get("role")  # "captain", "vice_captain", or "none"

    if not user_id or role not in ("captain", "vice_captain", "none"):
        messages.error(request, "Invalid request.")
        return redirect("events:event_detail", pk=event_pk)

    target_user = get_object_or_404(User, pk=int(user_id))

    if role == "captain":
        # A user holds at most one leadership role per squad; promote to captain.
        squad.vice_captains.remove(target_user)
        squad.captains.add(target_user)
        logfire.info(
            "Squad captain added",
            event_id=event_pk,
            squad_id=squad_pk,
            captain_id=target_user.id,
            admin_user_id=request.user.id,
        )
        _assign_discord_role(
            target_user, squad.discord_captain_role, f"{squad.name} Captain", admin_user_id=request.user.id
        )
    elif role == "vice_captain":
        squad.captains.remove(target_user)
        squad.vice_captains.add(target_user)
        logfire.info(
            "Squad vice captain added",
            event_id=event_pk,
            squad_id=squad_pk,
            vice_captain_id=target_user.id,
            admin_user_id=request.user.id,
        )
        _assign_discord_role(
            target_user, squad.discord_captain_role, f"{squad.name} Captain", admin_user_id=request.user.id
        )
    elif role == "none":
        was_leader = (
            squad.captains.filter(pk=target_user.pk).exists()
            or squad.vice_captains.filter(pk=target_user.pk).exists()
        )
        if was_leader:
            squad.captains.remove(target_user)
            squad.vice_captains.remove(target_user)
            logfire.info(
                "Squad captain role removed",
                event_id=event_pk,
                squad_id=squad_pk,
                user_id=target_user.id,
                admin_user_id=request.user.id,
            )
            _unassign_discord_role(target_user, squad.discord_captain_role, admin_user_id=request.user.id)

    if request.headers.get("HX-Request"):
        # Manage-squads page: re-render just this squad's self-contained roster panel
        if "squads/manage" in request.headers.get("HX-Current-URL", ""):
            return _render_manage_squad_panel(request, event, squad_pk)

        squad_members_data = _enrich_squad_members(event)
        signup_by_user = dict(EventSignup.objects.filter(event=event).values_list("user_id", "pk"))
        for members in squad_members_data.values():
            for member in members:
                member["signup_id"] = signup_by_user.get(member["user"].pk)

        from apps.team.models import DiscordRole

        role_ids = set()
        for sq in event.squads.all():
            if sq.team_discord_role:
                role_ids.add(str(sq.team_discord_role))
        if event.event_role:
            role_ids.add(str(event.event_role))
        role_names = (
            dict(DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")) if role_ids else {}
        )
        event_role_name = role_names.get(str(event.event_role), "") if event.event_role else ""

        # Refresh squad from DB to get updated captain/vice_captain
        squad.refresh_from_db()
        squad = Squad.objects.prefetch_related("captains", "vice_captains").get(pk=squad_pk)
        squad.role_name = role_names.get(str(squad.team_discord_role), "") if squad.team_discord_role else ""

        panel_html = render_to_string(
            "events/_squad_panel.html",
            {
                "squad": squad,
                "members": squad_members_data.get(squad.pk, []),
                "event_role_name": event_role_name,
                "event_pk": event_pk,
                "oob": False,
                "is_event_admin": True,
            },
            request=request,
        )

        # OOB update for role badges of the affected user
        target_user.refresh_from_db(fields=["discord_roles"])
        role_badges = _build_role_badges(target_user, event)
        signup_pk = signup_by_user.get(target_user.pk)
        if signup_pk:
            panel_html += render_to_string(
                "events/_role_badges.html",
                {"signup_id": signup_pk, "role_badges": role_badges, "oob": True},
                request=request,
            )

        return HttpResponse(panel_html)

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

    if not _can_manage_squad_availability(request.user, squad):
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


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def availability_edit_view(request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str) -> HttpResponse:
    """Edit a draft availability grid in the builder.

    GET pre-fills the builder from the stored grid (converted back to its own timezone).
    POST re-validates and updates the grid in place. Only draft grids are editable, so no
    member responses can be affected.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.

    Returns:
        Rendered builder (GET) or JsonResponse (POST), or a redirect when not allowed.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized availability edit access",
            grid_id=str(grid.id),
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        if request.method == "POST":
            return JsonResponse({"error": "Permission denied."}, status=403)
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    if grid.status != AvailabilityGrid.Status.DRAFT:
        if request.method == "POST":
            return JsonResponse({"error": "Only draft grids can be edited."}, status=400)
        messages.error(request, "Only draft grids can be edited.")
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)

    if request.method == "POST":
        return _handle_availability_save(request, event, squad, grid=grid)

    # GET: pre-fill the builder from the stored (UTC) grid, converted back to its own timezone.
    local_start_date, local_end_date, local_start_time, local_end_time = convert_utc_to_local_config(
        grid.start_date, grid.end_date, grid.start_time, grid.end_time, grid.grid_timezone
    )
    grid_local = convert_grid_to_local(
        grid.dates, grid.start_time, grid.end_time, grid.slot_duration, grid.blocked_cells, grid.grid_timezone
    )
    blocked_local = [
        {"date": key.split("|")[0], "time": key.split("|")[1]} for key in grid_local["display_blocked"]
    ]
    initial_grid = {
        "start_date": local_start_date.isoformat(),
        "end_date": local_end_date.isoformat(),
        "start_time": local_start_time,
        "end_time": local_end_time,
        "timezone": grid.grid_timezone,
        "slot_duration": grid.slot_duration,
        "blocked_cells": blocked_local,
        "title": grid.title,
        "expires": grid.expires.isoformat() if grid.expires else "",
        "max_races_question": grid.max_races_question,
        "rest_days_question": grid.rest_days_question,
        "hide_empty_days": grid.hide_empty_days,
    }
    user_tz = getattr(request.user, "timezone", "") or "UTC"
    logfire.debug(
        "Availability builder opened for edit",
        grid_id=str(grid.id),
        user_id=request.user.id,
        event_id=event_pk,
        squad_id=squad_pk,
    )
    return render(
        request,
        "events/availability_builder.html",
        {
            "event": event,
            "squad": squad,
            "timezone_choices_json": json.dumps(TIMEZONE_CHOICES),
            "user_timezone": user_tz,
            "initial_grid_json": json.dumps(initial_grid),
            "page_heading": "Edit Availability Grid",
        },
    )


def _handle_availability_save(
    request: HttpRequest, event: Event, squad: Squad, grid: AvailabilityGrid | None = None
) -> JsonResponse:
    """Validate and persist an AvailabilityGrid from a JSON POST body.

    Args:
        request: The HTTP request with JSON body.
        event: The parent event.
        squad: The squad to attach the grid to.
        grid: An existing grid to update in place; when None a new draft grid is created.

    Returns:
        JsonResponse with grid id on success, or error details on failure.

    """
    hhmm_re = re.compile(r"^\d{2}:\d{2}$")

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError, ValueError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    # --- Parse & validate fields ---
    title = str(data.get("title", "")).strip()

    try:
        start_date = date.fromisoformat(str(data.get("start_date", "")))
        end_date = date.fromisoformat(str(data.get("end_date", "")))
    except ValueError, TypeError:
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
    except ValueError, TypeError:
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
        except ValueError, TypeError:
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

    field_values = {
        "title": title,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "slot_duration": slot_duration,
        "grid_timezone": grid_tz,
        "blocked_cells": blocked_cells,
        "max_races_question": bool(data.get("max_races_question", False)),
        "rest_days_question": bool(data.get("rest_days_question", False)),
        "hide_empty_days": bool(data.get("hide_empty_days", False)),
        "expires": expires,
    }

    if grid is None:
        grid = AvailabilityGrid.objects.create(
            squad=squad,
            status=AvailabilityGrid.Status.DRAFT,
            created_by=request.user,
            **field_values,
        )
        logfire.info(
            "Availability grid created",
            grid_id=str(grid.id),
            squad_id=squad.pk,
            event_id=event.pk,
            user_id=request.user.id,
        )
    else:
        for attr, value in field_values.items():
            setattr(grid, attr, value)
        grid.save()
        logfire.info(
            "Availability grid updated",
            grid_id=str(grid.id),
            squad_id=squad.pk,
            event_id=event.pk,
            user_id=request.user.id,
        )
    return JsonResponse({"id": str(grid.id), "status": "ok"})


def _expand_utc_dates(start_date: date, end_date: date) -> list[str]:
    """Return an inclusive list of ``YYYY-MM-DD`` strings between two dates.

    Args:
        start_date: First day in the range.
        end_date: Last day in the range (inclusive).

    Returns:
        Sorted list of ISO date strings spanning the range.

    """
    out: list[str] = []
    cur = start_date
    while cur <= end_date:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


@login_required
@team_member_required()
@require_POST
def availability_preview_view(request: HttpRequest, event_pk: int, squad_pk: int) -> JsonResponse:
    """Return a draft grid converted to the requesting user's local timezone.

    Used by the builder's Preview modal so the captain sees the same view a
    rider would. Accepts the same JSON payload shape as the save view; runs
    through the same UTC conversion + ``convert_grid_to_local`` pipeline but
    targets ``request.user.timezone`` instead of the grid's authoring tz.

    Args:
        request: The HTTP request with the draft grid JSON.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        JSON with ``display_dates``, ``display_time_slots``, ``display_blocked``
        (list of ``date|time`` keys), and ``display_timezone``.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not _can_manage_squad_availability(request.user, squad):
        return JsonResponse({"error": "Permission denied."}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    try:
        start_date = date.fromisoformat(str(data.get("start_date", "")))
        end_date = date.fromisoformat(str(data.get("end_date", "")))
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid start_date / end_date."}, status=400)
    start_time = str(data.get("start_time", ""))
    end_time = str(data.get("end_time", ""))
    try:
        slot_duration = int(data.get("slot_duration", 30))
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid slot_duration."}, status=400)
    if slot_duration not in (15, 30, 60):
        return JsonResponse({"error": "slot_duration must be 15, 30, or 60."}, status=400)

    grid_tz = str(data.get("timezone", "UTC")).strip() or "UTC"
    if grid_tz != "UTC" and grid_tz not in available_timezones():
        return JsonResponse({"error": f"Invalid timezone: {grid_tz}"}, status=400)

    blocked_cells = data.get("blocked_cells", [])
    if not isinstance(blocked_cells, list):
        return JsonResponse({"error": "blocked_cells must be a list."}, status=400)

    # 1. Round-trip the grid's local-tz definition into UTC, matching the
    #    save view's flow so the preview matches what a rider would see.
    if grid_tz != "UTC":
        utc_start_date, utc_end_date, utc_start_time, utc_end_time = convert_local_to_utc(
            start_date, end_date, start_time, end_time, grid_tz,
        )
        utc_blocked = convert_blocked_cells_to_utc(blocked_cells, grid_tz, slot_duration)
    else:
        utc_start_date, utc_end_date, utc_start_time, utc_end_time = (
            start_date, end_date, start_time, end_time,
        )
        utc_blocked = list(blocked_cells)

    utc_dates = _expand_utc_dates(utc_start_date, utc_end_date)

    # 2. Convert UTC → display timezone (preferring the user's profile tz).
    user_tz = (getattr(request.user, "timezone", "") or "").strip()
    display_tz = user_tz or grid_tz or "UTC"
    if display_tz != "UTC" and display_tz not in available_timezones():
        display_tz = "UTC"

    grid_data = convert_grid_to_local(utc_dates, utc_start_time, utc_end_time, slot_duration, utc_blocked, display_tz)

    if bool(data.get("hide_empty_days", False)):
        grid_data = drop_fully_blocked_days(grid_data)

    return JsonResponse({
        "display_dates": list(grid_data["display_dates"]),
        "display_time_slots": list(grid_data["display_time_slots"]),
        "display_blocked": sorted(grid_data["display_blocked"]),
        "display_timezone": display_tz,
    })


def _post_grid_published_notification(
    request: HttpRequest,
    event: Event,
    squad: Squad,
    grid: AvailabilityGrid,
) -> bool | str:
    """Post a Discord channel message announcing that a grid is now published.

    Returns ``True`` on success or a short error string on failure. Failures are
    logged via Logfire; the caller decides whether to surface the error to the
    user. The grid's publish state is independent of this call.

    Date markdown uses ``<t:UNIX:D>`` so each Discord viewer sees the calendar
    day rendered in their own client locale. To avoid date drift across
    timezones, the unix timestamp is anchored at noon in the grid's authoring
    timezone.

    Args:
        request: The HTTP request (used for ``build_absolute_uri``).
        event: Parent event.
        squad: Squad whose Discord channel/role we post to.
        grid: The just-published availability grid.

    Returns:
        ``True`` on success, otherwise a string describing the failure.

    """
    if not squad.discord_channel_id:
        return "Squad has no Discord channel"

    try:
        grid_tz = ZoneInfo(grid.grid_timezone) if grid.grid_timezone else ZoneInfo("UTC")
    except Exception:
        grid_tz = ZoneInfo("UTC")

    start_unix = int(datetime.combine(grid.start_date, time(12, 0), tzinfo=grid_tz).timestamp())
    end_unix = int(datetime.combine(grid.end_date, time(12, 0), tzinfo=grid_tz).timestamp())

    response_url = request.build_absolute_uri(
        reverse(
            "events:availability_respond",
            kwargs={"event_pk": event.pk, "squad_pk": squad.pk, "grid_pk": grid.pk},
        )
    )

    title = grid.title or "Availability Grid"
    role_mention = f"<@&{squad.team_discord_role}>" if squad.team_discord_role else ""
    lines = [
        "**New Availability Requested**",
        title,
        f"<t:{start_unix}:D> – <t:{end_unix}:D>",  # noqa: RUF001
        response_url,
    ]
    if role_mention:
        lines.extend(["", role_mention])
    body = "\n".join(lines)

    allowed_role_ids = [str(squad.team_discord_role)] if squad.team_discord_role else None

    with logfire.span(
        "availability_publish_notify",
        grid_id=str(grid.id),
        squad_id=squad.pk,
        channel_id=str(squad.discord_channel_id),
        role_id=str(squad.team_discord_role) if squad.team_discord_role else None,
    ):
        ok = send_discord_channel_message(
            squad.discord_channel_id,
            body,
            allowed_role_ids=allowed_role_ids,
        )
    if ok:
        logfire.info(
            "Availability grid publish notification posted",
            grid_id=str(grid.id),
            squad_id=squad.pk,
            channel_id=str(squad.discord_channel_id),
        )
        return True
    return "Discord API call failed (see Logfire)"


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

    if not _can_manage_squad_availability(request.user, squad):
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

    notify = request.POST.get("notify") == "1"
    if notify and new_status == AvailabilityGrid.Status.PUBLISHED:
        notification_result = _post_grid_published_notification(request, event, squad, grid)
        if notification_result is True:
            messages.success(
                request,
                f'Grid "{grid.title}" is now published. Squad notified in Discord.',
            )
        else:
            messages.warning(
                request,
                f'Grid "{grid.title}" is now published, but the Discord notification failed: '
                f'{notification_result}.',
            )
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)

    messages.success(request, f'Grid "{grid.title}" is now {grid.get_status_display().lower()}.')
    return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)


@login_required
@team_member_required()
@require_POST
def availability_delete_view(request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str) -> HttpResponse:
    """Delete an availability grid along with all its responses and slot selections.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.

    Returns:
        Redirect to squad availability page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized availability delete attempt",
            grid_id=str(grid.id),
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    grid_title = grid.title or "Availability Grid"
    response_count = grid.responses.count()
    selection_count = grid.slot_selections.count()
    grid.delete()

    logfire.info(
        "Availability grid deleted",
        grid_id=str(grid_pk),
        grid_title=grid_title,
        response_count=response_count,
        selection_count=selection_count,
        squad_id=squad_pk,
        event_id=event_pk,
        user_id=request.user.id,
    )
    messages.success(request, f'Availability grid "{grid_title}" deleted.')
    return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)


@login_required
@team_member_required()
@require_POST
def availability_copy_view(request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str) -> HttpResponse:
    """Clone an availability grid with new start/end dates.

    All other fields (times, slot duration, timezone, questions, blocked cells)
    are copied verbatim. Blocked cells are shifted by the delta between the
    source grid's start date and the new start date; cells whose shifted date
    falls outside the new range are dropped. The new grid is always created as
    a draft with ``expires=None`` and an auto-generated title.

    Args:
        request: The HTTP request with ``start_date`` and ``end_date`` POST fields.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The source availability grid UUID.

    Returns:
        Redirect to squad availability page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    source = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized availability copy attempt",
            grid_id=str(source.id),
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    try:
        new_start_date = date.fromisoformat(request.POST.get("start_date", ""))
        new_end_date = date.fromisoformat(request.POST.get("end_date", ""))
    except (ValueError, TypeError):
        messages.error(request, "Invalid or missing start/end date.")
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)

    if new_start_date > new_end_date:
        messages.error(request, "Start date must be on or before end date.")
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)
    if (new_end_date - new_start_date).days > 31:
        messages.error(request, "Date range cannot exceed 31 days.")
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)

    delta_days = (new_start_date - source.start_date).days
    shifted_blocked: list[dict] = []
    for cell in source.blocked_cells or []:
        raw_date = cell.get("date") if isinstance(cell, dict) else None
        if not raw_date:
            continue
        try:
            shifted_date = date.fromisoformat(str(raw_date)) + timedelta(days=delta_days)
        except (ValueError, TypeError):
            continue
        if new_start_date <= shifted_date <= new_end_date:
            shifted_blocked.append({"date": shifted_date.isoformat(), "time": cell.get("time", "")})

    new_grid = AvailabilityGrid.objects.create(
        squad=squad,
        title="",
        start_date=new_start_date,
        end_date=new_end_date,
        start_time=source.start_time,
        end_time=source.end_time,
        slot_duration=source.slot_duration,
        grid_timezone=source.grid_timezone,
        blocked_cells=shifted_blocked,
        max_races_question=source.max_races_question,
        rest_days_question=source.rest_days_question,
        expires=None,
        status=AvailabilityGrid.Status.DRAFT,
        created_by=request.user,
    )

    logfire.info(
        "Availability grid copied",
        source_grid_id=str(source.id),
        new_grid_id=str(new_grid.id),
        squad_id=squad_pk,
        event_id=event_pk,
        user_id=request.user.id,
        delta_days=delta_days,
        blocked_cells_kept=len(shifted_blocked),
        blocked_cells_source=len(source.blocked_cells or []),
    )
    messages.success(request, f'Created copy "{new_grid.title}" as a draft.')
    return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)


@login_required
@team_member_required()
@require_POST
def availability_template_create_view(request: HttpRequest, event_pk: int, squad_pk: int) -> JsonResponse:
    """Save the builder's current configuration as a reusable per-squad template.

    Accepts a JSON body with the grid shape (local times, timezone, slot duration, length,
    and the optional questions). Blocked cells and dates are intentionally not stored.

    Args:
        request: The HTTP request with a JSON body.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        JsonResponse with the template id on success, or error details on failure.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)

    if not _can_manage_squad_availability(request.user, squad):
        return JsonResponse({"error": "Permission denied."}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    name = str(data.get("name", "")).strip()
    if not name:
        return JsonResponse({"error": "Template name is required."}, status=400)

    hhmm_re = re.compile(r"^\d{2}:\d{2}$")
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

    try:
        length_days = int(data.get("length_days", 0))
    except (ValueError, TypeError):
        return JsonResponse({"error": "length_days must be an integer."}, status=400)
    if not 1 <= length_days <= 31:
        return JsonResponse({"error": "length_days must be between 1 and 31."}, status=400)

    grid_tz = str(data.get("timezone", "UTC")).strip() or "UTC"
    if grid_tz != "UTC" and grid_tz not in available_timezones():
        return JsonResponse({"error": f"Invalid timezone: {grid_tz}"}, status=400)

    template = AvailabilityGridTemplate.objects.create(
        squad=squad,
        name=name,
        start_time=start_time,
        end_time=end_time,
        grid_timezone=grid_tz,
        slot_duration=slot_duration,
        default_length_days=length_days,
        max_races_question=bool(data.get("max_races_question", False)),
        rest_days_question=bool(data.get("rest_days_question", False)),
        created_by=request.user,
    )
    logfire.info(
        "Availability template created",
        template_id=template.pk,
        squad_id=squad.pk,
        event_id=event.pk,
        user_id=request.user.id,
    )
    return JsonResponse({"id": template.pk, "status": "ok"})


@login_required
@team_member_required()
@require_POST
def availability_template_apply_view(
    request: HttpRequest, event_pk: int, squad_pk: int, template_pk: int
) -> HttpResponse:
    """Create a draft availability grid from a template and a chosen start date.

    The end date is derived from the template's ``default_length_days``. The template's local
    times are converted to UTC for the chosen dates (DST-aware), and a draft grid is created.

    Args:
        request: The HTTP request with a ``start_date`` POST field.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        template_pk: The template primary key.

    Returns:
        Redirect to the squad availability page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    template = get_object_or_404(AvailabilityGridTemplate, pk=template_pk, squad=squad)

    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized availability template apply attempt",
            template_id=template.pk,
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    try:
        start_date = date.fromisoformat(request.POST.get("start_date", ""))
    except (ValueError, TypeError):
        messages.error(request, "Invalid or missing start date.")
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)

    end_date = start_date + timedelta(days=template.default_length_days - 1)

    start_date_utc, end_date_utc, start_time_utc, end_time_utc = convert_local_to_utc(
        start_date,
        end_date,
        template.start_time,
        template.end_time,
        template.grid_timezone,
    )

    grid = AvailabilityGrid.objects.create(
        squad=squad,
        title="",
        start_date=start_date_utc,
        end_date=end_date_utc,
        start_time=start_time_utc,
        end_time=end_time_utc,
        slot_duration=template.slot_duration,
        grid_timezone=template.grid_timezone,
        blocked_cells=[],
        max_races_question=template.max_races_question,
        rest_days_question=template.rest_days_question,
        expires=None,
        status=AvailabilityGrid.Status.DRAFT,
        created_by=request.user,
    )
    logfire.info(
        "Availability grid created from template",
        template_id=template.pk,
        grid_id=str(grid.id),
        squad_id=squad.pk,
        event_id=event.pk,
        user_id=request.user.id,
    )
    messages.success(request, f'Created draft "{grid.title}" from template "{template.name}".')
    return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)


@login_required
@team_member_required()
@require_POST
def availability_template_delete_view(
    request: HttpRequest, event_pk: int, squad_pk: int, template_pk: int
) -> HttpResponse:
    """Delete a per-squad availability template.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        template_pk: The template primary key.

    Returns:
        Redirect to the squad availability page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    template = get_object_or_404(AvailabilityGridTemplate, pk=template_pk, squad=squad)

    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized availability template delete attempt",
            template_id=template.pk,
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    template_name = template.name
    template.delete()
    logfire.info(
        "Availability template deleted",
        squad_id=squad.pk,
        event_id=event.pk,
        user_id=request.user.id,
    )
    messages.success(request, f'Deleted template "{template_name}".')
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
        except json.JSONDecodeError, ValueError:
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
            except ValueError, TypeError:
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
            except ValueError, TypeError:
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

    grid_data = convert_grid_to_local(
        grid.dates, grid.start_time, grid.end_time, grid.slot_duration, grid.blocked_cells, display_tz
    )

    if grid.hide_empty_days:
        grid_data = drop_fully_blocked_days(grid_data)

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

    responder_users = [r.user for r in responses]
    responder_user_ids = {u.pk for u in responder_users}

    squad_member_users = list(
        User.objects
        .filter(squad_memberships__squad=squad, squad_memberships__status=SquadMember.Status.MEMBER)
        .order_by("first_name", "last_name")
    )
    non_responder_users = [u for u in squad_member_users if u.pk not in responder_user_ids]

    # Enrich responders + non-responders with ZP/ZR data (shared lookup)
    zwids = list({u.zwid for u in responder_users + non_responder_users if u.zwid})
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

    enriched_non_responders = []
    for user in non_responder_users:
        zp = zp_by_zwid.get(user.zwid)
        zr = zr_by_zwid.get(user.zwid)
        enriched_non_responders.append({
            "user": user,
            "display_name": user.get_full_name() or user.discord_username,
            "zwid": user.zwid,
            "is_race_ready": user.is_race_ready,
            "is_extra_verified": user.is_extra_verified,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "in_zwiftracing": zr is not None,
            "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
            "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
            "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
            "zr_age": getattr(zr, "age", "") or "" if zr else "",
        })

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

    grid_data = convert_grid_to_local(
        grid.dates, grid.start_time, grid.end_time, grid.slot_duration, grid.blocked_cells, display_tz
    )

    if grid.hide_empty_days:
        grid_data = drop_fully_blocked_days(grid_data)

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
        display_dates.append({
            "date_str": d.strftime("%b %-d"),
            "day_name": day_names[d.weekday()],
            "full_date": d_str,
        })

    # Build local→UTC mapping for each cell (so JS can send UTC back)
    cell_map = grid_data["cell_map"]  # local_key → {"date": utc_date, "time": utc_time}

    # Load existing slot selections
    slot_selections = list(grid.slot_selections.prefetch_related("selected_users", "substitutes"))
    slot_selection_by_utc_key = {}
    scheduled_count_by_user_id: dict[int, int] = {}
    for sel in slot_selections:
        utc_key = f"{sel.slot_date.isoformat()}|{sel.slot_time}"
        slot_selection_by_utc_key[utc_key] = sel
        for selected_user in sel.selected_users.all():
            scheduled_count_by_user_id[selected_user.pk] = (
                scheduled_count_by_user_id.get(selected_user.pk, 0) + 1
            )

    # Annotate the responders table with each rider's scheduled-race count.
    for entry in enriched_responders:
        entry["scheduled_count"] = scheduled_count_by_user_id.get(entry["user"].pk, 0)

    is_event_admin = _can_manage_squad_availability(request.user, squad)

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
            # Look up UTC coordinates for this local cell
            utc_info = cell_map.get(key, {})
            utc_date = utc_info.get("date", "")
            utc_time = utc_info.get("time", "")
            utc_key = f"{utc_date}|{utc_time}" if utc_date else ""
            selection = slot_selection_by_utc_key.get(utc_key)
            cells.append({
                "is_blocked": is_blocked,
                "count": count,
                "opacity": opacity,
                "dark_text": dark_text,
                "users": [user_by_id[uid] for uid in uids if uid in user_by_id],
                "utc_date": utc_date,
                "utc_time": utc_time,
                "selection_name": selection.name if selection else "",
                "selection_id": selection.pk if selection else None,
            })
        grid_rows.append({"time_slot": time_slot, "cells": cells})

    # Build JSON data for JS modal (keyed by UTC coords)
    utc_cell_users_json = dict(utc_cell_user_ids)
    user_data_json = {}
    for uid, entry in user_by_id.items():
        user_data_json[uid] = {
            "display_name": entry["display_name"],
            "zr_category": entry["zr_category"],
            "zr_rating": float(entry["zr_rating"]) if entry["zr_rating"] is not None else None,
            "is_race_ready": entry["is_race_ready"],
        }
    # Slot selections with selected user IDs for pre-filling the modal
    selections_json = {}
    for sel in slot_selections:
        utc_key = f"{sel.slot_date.isoformat()}|{sel.slot_time}"
        selections_json[utc_key] = {
            "id": sel.pk,
            "name": sel.name,
            "status": sel.status,
            "opponent": sel.opponent,
            "event_invite_url": sel.event_invite_url,
            "course_url": sel.course_url,
            "thread_link": sel.thread_link,
            "selected_user_ids": list(sel.selected_users.values_list("pk", flat=True)),
            "substitute_ids": list(sel.substitutes.values_list("pk", flat=True)),
        }

    # Build enriched slot selections for initial render
    enriched_selections = []
    for sel in slot_selections:
        enriched_sel_users = []
        for user in sel.selected_users.all():
            zp = zp_by_zwid.get(user.zwid)
            zr = zr_by_zwid.get(user.zwid)
            zp_cat = ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else ""
            zp_cat_w = ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else ""
            enriched_sel_users.append({
                "user": user,
                "display_name": user.get_full_name() or user.discord_username,
                "zwid": user.zwid,
                "is_race_ready": user.is_race_ready,
                "is_extra_verified": user.is_extra_verified,
                "in_zwiftpower": zp is not None,
                "zp_category": zp_cat,
                "zp_category_w": zp_cat_w,
                "in_zwiftracing": zr is not None,
                "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
                "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
                "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
                "zr_age": getattr(zr, "age", "") or "" if zr else "",
            })
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo

        utc_dt = dt.combine(sel.slot_date, dt.strptime(sel.slot_time, "%H:%M").time(), tzinfo=ZoneInfo("UTC"))  # noqa: DTZ007  # clock-only parse, no date used
        local_dt = utc_dt.astimezone(ZoneInfo(display_tz))
        enriched_selections.append({
            "selection": sel,
            "enriched_users": enriched_sel_users,
            "local_date": local_dt.strftime("%Y-%m-%d"),
            "local_time": local_dt.strftime("%H:%M"),
            "local_day": local_dt.strftime("%a"),
        })

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
            "enriched_non_responders": enriched_non_responders,
            "display_timezone": display_tz,
            "tz_is_default": tz_is_default,
            "is_event_admin": is_event_admin,
            "slot_selections_enriched": enriched_selections,
            "utc_cell_users_json": json.dumps(utc_cell_users_json),
            "user_data_json": json.dumps(user_data_json),
            "selections_json": json.dumps(selections_json),
        },
    )


@login_required
@team_member_required()
@require_POST
def sync_event_roles_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Sync Discord roles from server for all users signed up to an event.

    Fetches each signup user's actual Discord roles and updates the local cache.
    Redirects back to the referring page.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.

    Returns:
        Redirect to the referring page.

    """
    event = get_object_or_404(Event, pk=event_pk)

    # Require event admin or assign_roles/head captain permission
    if not (request.user.is_event_admin or request.user.is_superuser or _can_manage_event_roles(request.user, event)):
        messages.error(request, "You don't have permission to sync roles.")
        return redirect("events:event_detail", pk=event_pk)

    users = User.objects.filter(
        pk__in=event.signups.filter(status=EventSignup.Status.REGISTERED).values_list("user_id", flat=True),
        discord_id__isnull=False,
    ).exclude(discord_id="")

    synced = 0
    failed = 0
    for user in users:
        if sync_user_discord_roles(user):
            synced += 1
        else:
            failed += 1

    logfire.info(
        "Event roles synced from Discord",
        event_id=event_pk,
        synced=synced,
        failed=failed,
        admin_user_id=request.user.id,
    )
    if failed:
        messages.warning(request, f"Synced roles for {synced} users, {failed} failed.")
    else:
        messages.success(request, f"Synced Discord roles for {synced} users.")

    # Redirect back to referring page
    referer = request.META.get("HTTP_REFERER", "")
    if "assign-riders" in referer:
        return redirect("events:squad_assign_page", event_pk=event_pk)
    if "manage-roles" in referer:
        return redirect("events:manage_roles", event_pk=event_pk)
    return redirect("events:event_detail", pk=event_pk)


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
    role_names = (
        dict(DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")) if role_ids else {}
    )

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

    # Build list of roles the user will receive
    from apps.team.models import DiscordRole

    pending_roles = []
    role_ids_to_resolve = set()
    if event.event_role:
        role_ids_to_resolve.add(str(event.event_role))
    if squad.team_discord_role:
        role_ids_to_resolve.add(str(squad.team_discord_role))
    role_name_map = (
        dict(DiscordRole.objects.filter(role_id__in=role_ids_to_resolve).values_list("role_id", "name"))
        if role_ids_to_resolve
        else {}
    )
    if event.event_role:
        pending_roles.append({
            "label": "Event Role",
            "name": role_name_map.get(str(event.event_role), f"Role {event.event_role}"),
        })
    if squad.team_discord_role:
        pending_roles.append({
            "label": "Squad Role",
            "name": role_name_map.get(str(squad.team_discord_role), f"Role {squad.team_discord_role}"),
        })

    if request.method == "GET":
        members = (
            squad.squad_members
            .filter(status=SquadMember.Status.MEMBER)
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
                "pending_roles": pending_roles,
            },
        )

    # POST — accept the invite
    if already_member:
        messages.info(request, f"You're already a member of squad {squad.name}.")
        return redirect("events:my_events")

    # Enforce the squad's gender and category requirements before joining
    rider_zr = ""
    rider_zwift_cat = ""
    rider_womens_cat = ""
    if request.user.zwid:
        zr = ZRRider.objects.filter(zwid=request.user.zwid).first()
        rider_zr = getattr(zr, "race_current_category", "") or "" if zr else ""
        zp = ZPTeamRiders.objects.filter(zwid=request.user.zwid).first()
        if zp:
            rider_zwift_cat = ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp.div else ""
            rider_womens_cat = ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp.divw else ""
    for ok, reason in (
        squad.check_gender_eligibility(request.user.gender),
        squad.check_zwift_eligibility(rider_zwift_cat),
        squad.check_womens_zwift_eligibility(rider_womens_cat),
        squad.check_zr_eligibility(rider_zr),
    ):
        if ok:
            continue
        logfire.info(
            "Squad invite join blocked by squad requirements",
            squad_id=squad.pk,
            event_id=event.pk,
            user_id=request.user.id,
            rider_zr_category=rider_zr,
            rider_gender=request.user.gender,
            reason=reason,
        )
        messages.error(request, f"You can't join {squad.name}: {reason}.")
        return redirect("events:squad_invite", token=token)

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

    # Auto-assign Discord roles if the user has a linked Discord account
    if request.user.discord_id:
        roles_updated = dict(request.user.discord_roles or {})
        roles_to_assign = []
        if event.event_role and not request.user.has_discord_role(event.event_role):
            roles_to_assign.append((str(event.event_role), "Event Role"))
        if squad.team_discord_role and not request.user.has_discord_role(squad.team_discord_role):
            roles_to_assign.append((str(squad.team_discord_role), squad.name))

        for role_id_str, role_label in roles_to_assign:
            success = add_discord_role(request.user.discord_id, role_id_str)
            if success:
                roles_updated[role_id_str] = role_label
                logfire.info(
                    "Discord role auto-assigned via squad invite",
                    event_id=event.pk,
                    squad_id=squad.pk,
                    user_id=request.user.id,
                    role_id=role_id_str,
                    role_label=role_label,
                )
            else:
                logfire.error(
                    "Failed to auto-assign Discord role via squad invite",
                    event_id=event.pk,
                    squad_id=squad.pk,
                    user_id=request.user.id,
                    role_id=role_id_str,
                )
                messages.warning(request, f"Could not assign Discord role: {role_label}. Contact an admin.")

        if roles_to_assign:
            request.user.discord_roles = roles_updated
            request.user.save(update_fields=["discord_roles"])

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


@require_GET
@login_required
@discord_permission_required("event_admin", raise_exception=True)
def squad_assign_page_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Dedicated two-column page for assigning signups to squads.

    Left column shows enriched signups with inline assignment controls.
    Right column shows squad panels with current member lists.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.

    Returns:
        Rendered squad assignment page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    signups = event.signups.select_related("user").all()
    enriched_signups = _enrich_signups(signups, event=event)
    squads = list(
        event.squads.prefetch_related("captains", "vice_captains")
        .annotate(member_count=Count("squad_members"))
        .order_by("name")
    )
    squad_members_data = _enrich_squad_members(event) if squads else {}

    # Map user_id -> signup pk so squad panels can build remove forms
    signup_by_user = {s.user_id: s.pk for s in signups}
    for members in squad_members_data.values():
        for member in members:
            member["signup_id"] = signup_by_user.get(member["user"].pk)

    # Resolve Discord role names for event and squads
    from apps.team.models import DiscordRole

    role_ids = {str(s.team_discord_role) for s in squads if s.team_discord_role}
    if event.event_role:
        role_ids.add(str(event.event_role))
    role_names = (
        dict(DiscordRole.objects.filter(role_id__in=role_ids).values_list("role_id", "name")) if role_ids else {}
    )
    event.event_role_name = role_names.get(str(event.event_role), "") if event.event_role else ""

    for squad in squads:
        squad.enriched_members = squad_members_data.get(squad.pk, [])
        squad.role_name = role_names.get(str(squad.team_discord_role), "") if squad.team_discord_role else ""

    is_event_admin = request.user.is_event_admin or request.user.is_superuser
    logfire.debug("Squad assign page viewed", user_id=request.user.id, event_id=event_pk)
    return render(
        request,
        "events/squad_assign.html",
        {
            "event": event,
            "enriched_signups": enriched_signups,
            "squads": squads,
            "is_event_admin": is_event_admin,
        },
    )


def _extract_thread_id(thread_link: str) -> str | None:
    """Pull the thread (channel) id from a Discord thread URL.

    Discord stores thread/channel URLs as ``https://discord.com/channels/{guild_id}/{thread_id}``.
    The Discord messages API treats the thread id the same as a channel id.

    Args:
        thread_link: A Discord thread URL.

    Returns:
        The thread id as a string of digits, or None if the link can't be parsed.

    """
    if not thread_link:
        return None
    candidate = thread_link.rstrip("/").rsplit("/", 1)[-1]
    return candidate if candidate.isdigit() else None


def _set_slot_substitutes(selection: AvailabilitySlotSelection, request: HttpRequest) -> None:
    """Set a slot selection's substitutes M2M from the posted ``substitutes`` checkboxes.

    Args:
        selection: The slot selection to update.
        request: The request whose POST holds the selected substitute user PKs.

    """
    sub_ids = request.POST.getlist("substitutes")
    selection.substitutes.set(User.objects.filter(pk__in=sub_ids))


@require_GET
def race_calendar_ics_view(request: HttpRequest, token: str) -> HttpResponse:
    """Serve a scheduled race as a downloadable .ics calendar invite.

    Public on purpose: the ``token`` is an unguessable signed value, so a team
    member can add the race to their calendar straight from a Discord thread
    without logging in.

    Args:
        request: The HTTP request.
        token: Signed token encoding the race primary key.

    Returns:
        A ``text/calendar`` response.

    Raises:
        Http404: If the token is invalid or the race no longer exists.

    """
    pk = unsign_race_token(token)
    if pk is None:
        raise Http404("Invalid calendar link.")
    selection = get_object_or_404(
        AvailabilitySlotSelection.objects.select_related("grid", "grid__squad"), pk=pk
    )
    response = HttpResponse(build_race_ics(selection), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="race-{selection.pk}.ics"'
    return response


def _build_slot_thread_message(
    selection: AvailabilitySlotSelection,
    *,
    header: str | None = None,
    request: HttpRequest | None = None,
) -> tuple[str, list[str]]:
    """Build the Discord message body for a scheduled race thread.

    Used for both the starter message when a thread is created and for "updated"
    messages posted later. The body uses Discord's native timestamp markdown so
    each viewer sees the date/time in their own client timezone. Selected
    riders are pinged on one line, followed directly by the substitute (if
    set); the squad's captain and vice captain are appended below in their own
    block. All three role mentions are added to the mention list so they are
    notified even when not in the racing checkboxes.

    Args:
        selection: The slot selection record.
        header: Optional first line (e.g. "**🔄 Race details updated**").
        request: Current request, used to build absolute calendar links. When
            omitted, the "Add to calendar" line is skipped.

    Returns:
        Tuple of (message body, deduped list of Discord IDs to mention).

    """
    utc_dt = datetime.combine(
        selection.slot_date,
        datetime.strptime(selection.slot_time, "%H:%M").time(),  # noqa: DTZ007  # clock-only parse, no date used
        tzinfo=ZoneInfo("UTC"),
    )
    unix_ts = int(utc_dt.timestamp())
    rider_discord_ids = [u.discord_id for u in selection.selected_users.all() if u.discord_id]
    mentions = " ".join(f"<@{did}>" for did in rider_discord_ids)

    squad = selection.grid.squad
    captains = list(squad.captains.all())
    vice_captains = list(squad.vice_captains.all())
    captain_dids = [c.discord_id for c in captains if c.discord_id]
    vice_captain_dids = [vc.discord_id for vc in vice_captains if vc.discord_id]
    substitute_dids = [s.discord_id for s in selection.substitutes.all() if s.discord_id]
    ds_dids = [d.discord_id for d in selection.directeurs_sportifs.all() if d.discord_id]

    lines: list[str] = []
    if header:
        lines.append(header)
    lines.extend(
        [
            f"**{selection.name}**",
            f"<t:{unix_ts}:F> (<t:{unix_ts}:R>)",
            f"**Status:** {selection.get_status_display()}",
        ]
    )
    if selection.opponent:
        lines.append(f"**Opponent:** {selection.opponent}")
    if selection.event_invite_url:
        lines.append(f"**Event invite:** {selection.event_invite_url}")
    if selection.course_url:
        lines.append(f"**Course:** {selection.course_url}")
    if mentions or substitute_dids:
        lines.append("")
        if mentions:
            lines.append(mentions)
        if substitute_dids:
            label = "SUB" if len(substitute_dids) == 1 else "SUBS"
            lines.append(f"**{label}:** " + " ".join(f"<@{did}>" for did in substitute_dids))
    if captain_dids or vice_captain_dids:
        lines.append("")
        if captain_dids:
            label = "Captain" if len(captain_dids) == 1 else "Captains"
            lines.append(f"**{label}:** " + " ".join(f"<@{did}>" for did in captain_dids))
        if vice_captain_dids:
            label = "Vice Captain" if len(vice_captain_dids) == 1 else "Vice Captains"
            lines.append(f"**{label}:** " + " ".join(f"<@{did}>" for did in vice_captain_dids))
    if ds_dids:
        lines.append("")
        label = "DS" if len(ds_dids) == 1 else "DSs"
        lines.append(f"🎽 **{label}:** " + " ".join(f"<@{did}>" for did in ds_dids))

    if request is not None:
        cal = race_calendar_urls(selection, request)
        # Angle brackets suppress Discord's link previews for these long URLs.
        lines.append("")
        lines.append(f"📅 **Add to calendar:** Google: <{cal['gcal_url']}> · iCal: <{cal['ics_url']}>")

    # Discord IDs to include in allowed_user_ids — dedup while preserving order
    # so captains/vice-captains/substitutes actually get pinged even if they aren't racing.
    seen: set[str] = set()
    discord_ids: list[str] = []
    for did in (*rider_discord_ids, *substitute_dids, *captain_dids, *vice_captain_dids, *ds_dids):
        if did and did not in seen:
            seen.add(did)
            discord_ids.append(did)
    return "\n".join(lines), discord_ids


def _slot_thread_name(selection: AvailabilitySlotSelection, grid: AvailabilityGrid) -> str:
    """Build the Discord thread name for a scheduled race.

    Format is ``"{race name} {Mon D}"`` where the date is rendered in the grid's
    local timezone. Used both when creating a thread and when renaming an
    existing one so the two stay consistent.

    Args:
        selection: The slot selection record.
        grid: The parent availability grid (provides the display timezone).

    Returns:
        The thread name string (not yet truncated; Discord truncates to 100).

    """
    utc_dt = datetime.combine(
        selection.slot_date,
        datetime.strptime(selection.slot_time, "%H:%M").time(),  # noqa: DTZ007  # clock-only parse, no date used
        tzinfo=ZoneInfo("UTC"),
    )
    try:
        grid_tz = ZoneInfo(grid.grid_timezone) if grid.grid_timezone else ZoneInfo("UTC")
    except Exception:
        grid_tz = ZoneInfo("UTC")
    grid_local_dt = utc_dt.astimezone(grid_tz)
    return f"{selection.name} {grid_local_dt.strftime('%b %-d')}"


def _create_slot_thread(
    selection: AvailabilitySlotSelection,
    squad: Squad,
    grid: AvailabilityGrid,
    user: User,
    request: HttpRequest | None = None,
) -> str | None:
    """Create the Discord thread for a confirmed scheduled race.

    Validates the same prerequisites the standalone endpoint enforces (confirmed
    status, riders selected, squad channel, guild configured, no existing thread),
    posts the starter message, and persists the resulting thread URL onto the
    selection.

    Args:
        selection: The slot selection record (must be persisted).
        squad: The parent squad.
        grid: The parent availability grid.
        user: The acting user (recorded in logs).
        request: Current request, forwarded so the starter message can include
            absolute calendar links.

    Returns:
        None on success, or an error message string suitable for displaying to
        the admin.

    """
    if selection.thread_link:
        return "A thread already exists for this race."
    if not squad.discord_channel_id:
        return "Squad has no Discord channel configured."
    if selection.status != AvailabilitySlotSelection.Status.CONFIRMED:
        return "Race status must be Confirmed before creating a thread."
    if not selection.selected_users.exists():
        return "Select at least one rider before creating a thread."

    guild_id = config.GUILD_ID
    if not guild_id:
        return "Discord guild is not configured."

    thread_name = _slot_thread_name(selection, grid)

    message_body, discord_ids = _build_slot_thread_message(selection, request=request)

    thread_id, error = create_discord_thread(squad.discord_channel_id, thread_name)
    if thread_id is None:
        logfire.error(
            "Failed to create Discord thread for scheduled race",
            selection_id=selection.pk,
            channel_id=str(squad.discord_channel_id),
            error=error,
        )
        return f"Failed to create thread: {error}"

    posted = send_discord_channel_message(
        thread_id,
        message_body,
        allowed_user_ids=discord_ids or None,
    )
    if not posted:
        logfire.warning(
            "Discord thread created but starter message failed to post",
            selection_id=selection.pk,
            thread_id=str(thread_id),
        )

    selection.thread_link = f"https://discord.com/channels/{guild_id}/{thread_id}"
    selection.save(update_fields=["thread_link", "updated_at"])

    logfire.info(
        "Scheduled race Discord thread created",
        selection_id=selection.pk,
        thread_id=str(thread_id),
        channel_id=str(squad.discord_channel_id),
        mention_count=len(discord_ids),
        user_id=user.id,
    )
    return None


def _post_slot_thread_update(
    selection: AvailabilitySlotSelection, user: User, request: HttpRequest | None = None
) -> str | None:
    """Post an "updated" message to the existing thread for a scheduled race.

    Args:
        selection: The slot selection record (must already have ``thread_link`` set).
        user: The acting user (recorded in logs).
        request: Current request, forwarded so the update message can include
            absolute calendar links.

    Returns:
        None on success, or an error message string.

    """
    thread_id = _extract_thread_id(selection.thread_link or "")
    if not thread_id:
        return "Could not determine the Discord thread id from the saved link."

    message_body, discord_ids = _build_slot_thread_message(
        selection, header="**🔄 Race details updated**", request=request
    )
    posted = send_discord_channel_message(
        thread_id,
        message_body,
        allowed_user_ids=discord_ids or None,
    )
    if not posted:
        logfire.error(
            "Failed to post update message to scheduled race thread",
            selection_id=selection.pk,
            thread_id=thread_id,
        )
        return "Failed to post update message to the thread."

    logfire.info(
        "Scheduled race update posted to thread",
        selection_id=selection.pk,
        thread_id=thread_id,
        mention_count=len(discord_ids),
        user_id=user.id,
    )
    return None


@login_required
@team_member_required()
@require_POST
def slot_selection_create_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str
) -> HttpResponse:
    """Create or update an availability slot selection for a specific cell.

    When ``create_thread=1`` is included in the POST body, the view also creates
    the Discord thread immediately after persisting the slot. This powers the
    "Save & Create Thread" button on new (unsaved) cells.

    Args:
        request: The HTTP request with name, slot_date, slot_time, selected_users[].
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.

    Returns:
        HTMX partial with updated slot selections container, or 400 when
        ``create_thread`` is requested but thread creation fails.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    name = request.POST.get("name", "").strip()
    slot_date = request.POST.get("slot_date", "")
    slot_time = request.POST.get("slot_time", "")
    opponent = request.POST.get("opponent", "").strip()
    event_invite_url = request.POST.get("event_invite_url", "").strip()
    course_url = request.POST.get("course_url", "").strip()
    thread_link = request.POST.get("thread_link", "").strip()
    raw_status = request.POST.get("status", AvailabilitySlotSelection.Status.NONE)
    valid_statuses = {s.value for s in AvailabilitySlotSelection.Status}
    status = raw_status if raw_status in valid_statuses else AvailabilitySlotSelection.Status.NONE
    selected_user_ids = request.POST.getlist("selected_users")
    also_create_thread = request.POST.get("create_thread") == "1"

    if not name or not slot_date or not slot_time:
        return HttpResponse("Name, date, and time are required.", status=400)

    selection, created = AvailabilitySlotSelection.objects.update_or_create(
        grid=grid,
        slot_date=slot_date,
        slot_time=slot_time,
        defaults={
            "name": name,
            "status": status,
            "opponent": opponent,
            "event_invite_url": event_invite_url,
            "course_url": course_url,
            "thread_link": thread_link,
            "created_by": request.user,
        },
    )
    selection.selected_users.set(User.objects.filter(pk__in=selected_user_ids))
    _set_slot_substitutes(selection, request)

    logfire.info(
        "Slot selection created" if created else "Slot selection updated",
        grid_id=str(grid.id),
        selection_id=selection.pk,
        slot_date=slot_date,
        slot_time=slot_time,
        user_id=request.user.id,
        selected_count=len(selected_user_ids),
    )

    if also_create_thread and not selection.thread_link:
        error = _create_slot_thread(selection, squad, grid, request.user, request=request)
        if error:
            return HttpResponse(error, status=400)

    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_update_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Update an existing slot selection's name and selected users.

    Args:
        request: The HTTP request with name, selected_users[].
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    name = request.POST.get("name", "").strip()
    opponent = request.POST.get("opponent", "").strip()
    event_invite_url = request.POST.get("event_invite_url", "").strip()
    course_url = request.POST.get("course_url", "").strip()
    thread_link = request.POST.get("thread_link", "").strip()
    raw_status = request.POST.get("status", selection.status)
    valid_statuses = {s.value for s in AvailabilitySlotSelection.Status}
    status = raw_status if raw_status in valid_statuses else selection.status
    selected_user_ids = request.POST.getlist("selected_users")

    if not name:
        return HttpResponse("Name is required.", status=400)

    selection.name = name
    selection.status = status
    selection.opponent = opponent
    selection.event_invite_url = event_invite_url
    selection.course_url = course_url
    selection.thread_link = thread_link
    selection.save(
        update_fields=[
            "name",
            "status",
            "opponent",
            "event_invite_url",
            "course_url",
            "thread_link",
            "updated_at",
        ]
    )
    selection.selected_users.set(User.objects.filter(pk__in=selected_user_ids))
    _set_slot_substitutes(selection, request)

    logfire.info(
        "Slot selection updated",
        grid_id=str(grid.id),
        selection_id=selection.pk,
        user_id=request.user.id,
        selected_count=len(selected_user_ids),
    )

    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_delete_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Delete a slot selection.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    logfire.info(
        "Slot selection deleted",
        grid_id=str(grid.id),
        selection_id=selection.pk,
        slot_date=str(selection.slot_date),
        slot_time=selection.slot_time,
        user_id=request.user.id,
    )
    selection.delete()

    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_create_thread_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Create a Discord thread in the squad's channel for a confirmed scheduled race.

    Posts a starting message that names the race, lists the date/time, and @-mentions
    the selected riders. Saves the resulting thread URL onto the slot selection.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container, or 400 with an error
        message if validation fails or the Discord API call errors out.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    # Persist any unsaved edits from the modal before reading state below.
    # The button posts the full form so the user does not have to "Save Race"
    # before "Create Discord Thread".
    posted_name = request.POST.get("name", "").strip()
    if posted_name:
        valid_statuses = {s.value for s in AvailabilitySlotSelection.Status}
        raw_status = request.POST.get("status", selection.status)
        new_status = raw_status if raw_status in valid_statuses else selection.status
        selection.name = posted_name
        selection.status = new_status
        selection.opponent = request.POST.get("opponent", "").strip()
        selection.event_invite_url = request.POST.get("event_invite_url", "").strip()
        selection.course_url = request.POST.get("course_url", "").strip()
        # thread_link is intentionally NOT overwritten here — it's set below
        # once the thread is created. Letting the form's stale value land would
        # break the idempotence guard on the next click.
        selection.save(
            update_fields=[
                "name",
                "status",
                "opponent",
                "event_invite_url",
                "course_url",
                "updated_at",
            ]
        )
        if "selected_users" in request.POST:
            selected_user_ids = request.POST.getlist("selected_users")
            selection.selected_users.set(User.objects.filter(pk__in=selected_user_ids))
        if "substitutes" in request.POST:
            _set_slot_substitutes(selection, request)

    error = _create_slot_thread(selection, squad, grid, request.user, request=request)
    if error:
        return HttpResponse(error, status=400)

    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_post_update_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Persist edits to a scheduled race and post an update message to its thread.

    Powers the "Save & Post Update" button on existing slots that already have a
    Discord thread. Saves the form values the same way ``slot_selection_update_view``
    does, then posts a Discord message into the thread summarizing the latest details.

    Args:
        request: The HTTP request with the full slot form payload.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container, or 400 if validation
        fails or the Discord post errors out.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    if not selection.thread_link:
        return HttpResponse("This race has no Discord thread yet — create one first.", status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return HttpResponse("Name is required.", status=400)

    valid_statuses = {s.value for s in AvailabilitySlotSelection.Status}
    raw_status = request.POST.get("status", selection.status)
    status = raw_status if raw_status in valid_statuses else selection.status

    selection.name = name
    selection.status = status
    selection.opponent = request.POST.get("opponent", "").strip()
    selection.event_invite_url = request.POST.get("event_invite_url", "").strip()
    selection.course_url = request.POST.get("course_url", "").strip()
    # thread_link is preserved — the form value can be stale, but the saved one
    # is what we trust for posting back into the thread.
    selection.save(
        update_fields=[
            "name",
            "status",
            "opponent",
            "event_invite_url",
            "course_url",
            "updated_at",
        ]
    )
    if "selected_users" in request.POST:
        selected_user_ids = request.POST.getlist("selected_users")
        selection.selected_users.set(User.objects.filter(pk__in=selected_user_ids))
    if "substitutes" in request.POST:
        _set_slot_substitutes(selection, request)

    error = _post_slot_thread_update(selection, request.user, request=request)
    if error:
        return HttpResponse(error, status=400)

    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_rename_thread_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Rename the Discord thread for a scheduled race to match its current name.

    Persists the posted race name first (so a just-edited name is reflected),
    then renames the thread to ``"{race name} {Mon D}"``. Skips the Discord call
    when the name is unchanged to avoid burning Discord's rename rate limit.

    Args:
        request: The HTTP request with the slot form payload (``name`` is used).
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container, or 400 on error.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    if not selection.thread_link:
        return HttpResponse("This race has no Discord thread yet — create one first.", status=400)

    thread_id = _extract_thread_id(selection.thread_link)
    if not thread_id:
        return HttpResponse("Could not determine the Discord thread id from the saved link.", status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return HttpResponse("Name is required.", status=400)

    old_name = _slot_thread_name(selection, grid)
    if name != selection.name:
        selection.name = name
        selection.save(update_fields=["name", "updated_at"])
    new_name = _slot_thread_name(selection, grid)

    if new_name == old_name:
        # Nothing to rename — avoid spending the Discord rename rate limit.
        return _render_slot_selections_partial(request, event, squad, grid)

    ok, error = rename_discord_thread(thread_id, new_name)
    if not ok:
        logfire.error(
            "Failed to rename scheduled race Discord thread",
            selection_id=selection.pk,
            thread_id=thread_id,
            error=error,
        )
        return HttpResponse(f"Failed to rename thread: {error}", status=400)

    logfire.info(
        "Scheduled race Discord thread renamed",
        selection_id=selection.pk,
        thread_id=thread_id,
        user_id=request.user.id,
    )
    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_archive_thread_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Archive (close) the Discord thread for a scheduled race.

    Archiving is reversible — the thread and its history are preserved and the
    saved ``thread_link`` is left intact so it keeps working.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container, or 400 on error.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    thread_id = _extract_thread_id(selection.thread_link or "")
    if not thread_id:
        return HttpResponse("This race has no Discord thread to archive.", status=400)

    ok, error = archive_discord_thread(thread_id)
    if not ok:
        logfire.error(
            "Failed to archive scheduled race Discord thread",
            selection_id=selection.pk,
            thread_id=thread_id,
            error=error,
        )
        return HttpResponse(f"Failed to archive thread: {error}", status=400)

    logfire.info(
        "Scheduled race Discord thread archived",
        selection_id=selection.pk,
        thread_id=thread_id,
        user_id=request.user.id,
    )
    return _render_slot_selections_partial(request, event, squad, grid)


@login_required
@team_member_required()
@require_POST
def slot_selection_delete_thread_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Permanently delete the Discord thread for a scheduled race.

    Deletes the thread and clears the saved ``thread_link`` so the slot no longer
    points at a dead URL. The slot itself is left in place. Irreversible.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.
        grid_pk: The availability grid UUID.
        slot_pk: The slot selection primary key.

    Returns:
        HTMX partial with updated slot selections container, or 400 on error.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)

    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    thread_id = _extract_thread_id(selection.thread_link or "")
    if not thread_id:
        return HttpResponse("This race has no Discord thread to delete.", status=400)

    ok, error = delete_discord_thread(thread_id)
    if not ok:
        logfire.error(
            "Failed to delete scheduled race Discord thread",
            selection_id=selection.pk,
            thread_id=thread_id,
            error=error,
        )
        return HttpResponse(f"Failed to delete thread: {error}", status=400)

    selection.thread_link = ""
    selection.save(update_fields=["thread_link", "updated_at"])

    logfire.info(
        "Scheduled race Discord thread deleted",
        selection_id=selection.pk,
        thread_id=thread_id,
        user_id=request.user.id,
    )
    return _render_slot_selections_partial(request, event, squad, grid)


def _render_slot_selections_partial(
    request: HttpRequest, event: Event, squad: Squad, grid: AvailabilityGrid
) -> HttpResponse:
    """Render the slot selections partial for HTMX responses.

    Args:
        request: The HTTP request.
        event: The parent event.
        squad: The squad.
        grid: The availability grid.

    Returns:
        Rendered HTML partial of all slot selection cards.

    """
    from apps.zwiftpower.models import ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    selections = list(grid.slot_selections.prefetch_related("selected_users", "directeurs_sportifs"))
    is_event_admin = _can_manage_squad_availability(request.user, squad)

    # Determine display timezone
    user_tz = getattr(request.user, "timezone", "") or ""
    display_tz = user_tz or grid.grid_timezone or "UTC"

    # Enrich selected users with ZP/ZR data
    all_selected_users = set()
    for sel in selections:
        all_selected_users.update(sel.selected_users.all())
    zwids = [u.zwid for u in all_selected_users if u.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    zr_by_zwid = {r.zwid: r for r in ZRRider.objects.filter(zwid__in=zwids)} if zwids else {}

    enriched_selections = []
    for sel in selections:
        enriched_users = []
        for user in sel.selected_users.all():
            zp = zp_by_zwid.get(user.zwid)
            zr = zr_by_zwid.get(user.zwid)
            zp_cat = ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else ""
            zp_cat_w = ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else ""
            enriched_users.append({
                "user": user,
                "display_name": user.get_full_name() or user.discord_username,
                "zwid": user.zwid,
                "is_race_ready": user.is_race_ready,
                "is_extra_verified": user.is_extra_verified,
                "in_zwiftpower": zp is not None,
                "zp_category": zp_cat,
                "zp_category_w": zp_cat_w,
                "in_zwiftracing": zr is not None,
                "zr_category": getattr(zr, "race_current_category", "") or "" if zr else "",
                "zr_rating": getattr(zr, "race_current_rating", None) if zr else None,
                "zr_phenotype": getattr(zr, "phenotype_value", "") or "" if zr else "",
                "zr_age": getattr(zr, "age", "") or "" if zr else "",
            })
        # Convert slot time to display timezone
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo

        utc_dt = dt.combine(sel.slot_date, dt.strptime(sel.slot_time, "%H:%M").time(), tzinfo=ZoneInfo("UTC"))  # noqa: DTZ007  # clock-only parse, no date used
        local_dt = utc_dt.astimezone(ZoneInfo(display_tz))
        ds_list = [
            {"user": d, "display_name": d.get_full_name() or d.discord_username}
            for d in sel.directeurs_sportifs.all()
        ]
        enriched_selections.append({
            "selection": sel,
            "enriched_users": enriched_users,
            "ds_list": ds_list,
            "local_date": local_dt.strftime("%Y-%m-%d"),
            "local_time": local_dt.strftime("%H:%M"),
            "local_day": local_dt.strftime("%a"),
        })

    html = render_to_string(
        "events/_slot_selections_container.html",
        {
            "enriched_selections": enriched_selections,
            "event": event,
            "squad": squad,
            "grid": grid,
            "is_event_admin": is_event_admin,
            "display_timezone": display_tz,
        },
        request=request,
    )
    return HttpResponse(html)


def _render_ds_list(request: HttpRequest, event: Event, squad: Squad, grid: AvailabilityGrid, selection) -> str:
    """Render the DS chips list partial (target ``#ds-list-<pk>``) for a slot.

    Returns:
        The rendered ``_slot_ds_list.html`` HTML string.

    """
    ds_list = [
        {"user": d, "display_name": d.get_full_name() or d.discord_username}
        for d in selection.directeurs_sportifs.all()
    ]
    return render_to_string(
        "events/_slot_ds_list.html",
        {"selection": selection, "ds_list": ds_list, "event": event, "squad": squad, "grid": grid},
        request=request,
    )


def _post_ds_added_to_thread(selection, ds_user) -> None:
    """If the race has a Discord thread, post a short note @-mentioning the newly added DS."""
    if not selection.thread_link or not ds_user.discord_id:
        return
    thread_id = selection.thread_link.rstrip("/").split("/")[-1]
    if not thread_id.isdigit():
        return
    name = ds_user.get_full_name() or ds_user.discord_username or "DS"
    send_discord_channel_message(
        int(thread_id),
        f"🎽 **DS added:** <@{ds_user.discord_id}> ({name})",
        allowed_user_ids=[ds_user.discord_id],
    )


@login_required
@team_member_required()
@require_GET
def slot_ds_search_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int
) -> HttpResponse:
    """Datastar: search team members to add as DS, patching the results list for a slot.

    Returns:
        A Datastar SSE response patching the slot's ``#ds-results-<pk>`` element.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)
    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2:
        existing = set(selection.directeurs_sportifs.values_list("pk", flat=True))
        users = (
            User.objects.filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(discord_username__icontains=q)
                | Q(discord_nickname__icontains=q)
            )
            .exclude(pk__in=existing)
            .filter(discord_id__isnull=False)
            .exclude(discord_id="")
            .order_by("first_name", "last_name")[:8]
        )
        results = [{"user": u, "display_name": u.get_full_name() or u.discord_username} for u in users]

    html = render_to_string(
        "events/_slot_ds_results.html",
        {"selection": selection, "results": results, "event": event, "squad": squad, "grid": grid, "query": q},
        request=request,
    )
    return DatastarResponse(ServerSentEventGenerator.patch_elements(html))


@login_required
@team_member_required()
@require_POST
def slot_ds_add_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int, user_id: int
) -> HttpResponse:
    """Datastar: add a DS to a race — assign the squad role, mention in the thread, patch the list.

    Returns:
        A Datastar SSE response patching the slot's DS list and clearing the search results.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)
    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    ds_user = get_object_or_404(User, pk=user_id)
    ds, created = SlotDS.objects.get_or_create(
        selection=selection, user=ds_user, defaults={"added_by": request.user}
    )
    if created:
        if ds_service.assign_squad_role(ds_user, squad, actor_id=request.user.id):
            ds.role_was_assigned = True
            ds.save(update_fields=["role_was_assigned"])
        _post_ds_added_to_thread(selection, ds_user)
        logfire.info(
            "DS added to race",
            selection_id=selection.pk,
            ds_user_id=ds_user.id,
            squad_id=squad.pk,
            role_assigned=ds.role_was_assigned,
            admin_user_id=request.user.id,
        )

    list_html = _render_ds_list(request, event, squad, grid, selection)
    cleared_results = f'<div id="ds-results-{selection.pk}"></div>'

    def events_gen():
        yield ServerSentEventGenerator.patch_elements(list_html)
        yield ServerSentEventGenerator.patch_elements(cleared_results)

    return DatastarResponse(events_gen())


@login_required
@team_member_required()
@require_POST
def slot_ds_remove_view(
    request: HttpRequest, event_pk: int, squad_pk: int, grid_pk: str, slot_pk: int, user_id: int
) -> HttpResponse:
    """Datastar: remove a DS from a race — strip the squad role if safe, patch the list.

    Returns:
        A Datastar SSE response patching the slot's DS list.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    grid = get_object_or_404(AvailabilityGrid, pk=grid_pk, squad=squad)
    selection = get_object_or_404(AvailabilitySlotSelection, pk=slot_pk, grid=grid)
    if not _can_manage_squad_availability(request.user, squad):
        return HttpResponse("Permission denied", status=403)

    ds = SlotDS.objects.filter(selection=selection, user_id=user_id).select_related("user").first()
    if ds:
        if (
            ds.role_was_assigned
            and ds.role_removed_at is None
            and ds_service.should_remove_squad_role(ds.user, squad, exclude_slot_ds_pk=ds.pk)
        ):
            ds_service.remove_squad_role(ds.user, squad, actor_id=request.user.id)
        logfire.info(
            "DS removed from race",
            selection_id=selection.pk,
            ds_user_id=ds.user_id,
            squad_id=squad.pk,
            admin_user_id=request.user.id,
        )
        ds.delete()

    list_html = _render_ds_list(request, event, squad, grid, selection)
    return DatastarResponse(ServerSentEventGenerator.patch_elements(list_html))


def _can_add_members(user: User, event: Event) -> bool:
    """Check if user can add members to an event.

    Allowed for head captains, event admins, app admins, and superusers.

    Args:
        user: The requesting user.
        event: The event.

    Returns:
        True if the user can add members.

    """
    if user.is_superuser or user.has_permission(Permissions.APP_ADMIN):
        return True
    return bool(event.head_captain_role_id and user.has_discord_role(event.head_captain_role_id))


@require_GET
@login_required
@team_member_required()
def add_members_search_view(request: HttpRequest, event_pk: int) -> JsonResponse:
    """Search team members for the Add Members modal.

    Returns JSON list of users matching the query who are not already signed up.

    Args:
        request: The HTTP request with 'q' query parameter.
        event_pk: The event primary key.

    Returns:
        JSON response with matching users.

    """
    event = get_object_or_404(Event, pk=event_pk)
    if not _can_add_members(request.user, event):
        return JsonResponse({"results": []}, status=403)

    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    existing_user_ids = set(EventSignup.objects.filter(event=event).values_list("user_id", flat=True))

    users = User.objects.filter(
        Q(first_name__icontains=q)
        | Q(last_name__icontains=q)
        | Q(discord_username__icontains=q)
        | Q(discord_nickname__icontains=q)
    ).exclude(pk__in=existing_user_ids).filter(discord_id__isnull=False).exclude(discord_id="")[:20]

    results = []
    for u in users:
        display = u.get_full_name() or u.discord_username or u.discord_nickname or str(u.pk)
        results.append({
            "id": u.pk,
            "display_name": display,
            "discord_username": u.discord_username or "",
        })
    return JsonResponse({"results": results})


@login_required
@team_member_required()
@require_POST
def add_members_view(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Add selected members to event: create signup and assign event Discord role.

    Only accessible to head captains and event admins.

    Args:
        request: The HTTP request with 'user_ids' POST data.
        event_pk: The event primary key.

    Returns:
        Redirect to event detail page.

    """
    event = get_object_or_404(Event, pk=event_pk)
    if not _can_add_members(request.user, event):
        messages.error(request, "You don't have permission to add members.")
        return redirect("events:event_detail", pk=event_pk)

    user_ids = request.POST.getlist("user_ids")
    if not user_ids:
        messages.warning(request, "No members selected.")
        return redirect("events:event_detail", pk=event_pk)

    existing_user_ids = set(EventSignup.objects.filter(event=event).values_list("user_id", flat=True))
    users_to_add = User.objects.filter(pk__in=user_ids).exclude(pk__in=existing_user_ids)

    added_count = 0
    role_count = 0
    from apps.team.models import DiscordRole

    event_role_name = ""
    if event.event_role:
        dr = DiscordRole.objects.filter(role_id=str(event.event_role)).first()
        event_role_name = dr.name if dr else "Event Role"

    from apps.events.tasks import enqueue_signup_notification

    for user in users_to_add:
        signup = EventSignup.objects.create(event=event, user=user)
        added_count += 1
        enqueue_signup_notification(signup, request=request)

        if event.event_role:
            result = _assign_discord_role(
                user, event.event_role, event_role_name, admin_user_id=request.user.id
            )
            if result is True:
                role_count += 1

    logfire.info(
        "Members added to event by head captain",
        event_id=event_pk,
        event_title=event.title,
        admin_user_id=request.user.id,
        added_count=added_count,
        role_assigned_count=role_count,
    )
    msg = f"Added {added_count} member{'s' if added_count != 1 else ''} to the event."
    if event.event_role and role_count:
        msg += f" Assigned event role to {role_count}."
    messages.success(request, msg)
    return redirect("events:event_detail", pk=event_pk)
