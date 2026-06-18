"""Views for the TTT planner.

The main page is a server-rendered DaisyUI page; mutations are HTMX POSTs that
return the ``_plan_body`` partial (roster table + results panel), keeping the
page in sync after every edit. CSRF is supplied globally via ``hx-headers`` on
``<body>`` in base.html.
"""

from __future__ import annotations

import contextlib

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import team_member_required
from apps.events import squads as event_squads
from apps.events.models import Squad
from apps.ttt_planner import terrain
from apps.ttt_planner.models import PlanRider, Route, RouteGpx, TttPlan
from apps.ttt_planner.services import roster, zwiftgopher, zwiftgopher_client
from apps.ttt_planner.services.compute import (
    compute_auto_balance,
    compute_plan,
    quick_finish_time,
    sustainable_speed,
)
from apps.ttt_planner.services.gpx import parse_gpx
from apps.ttt_planner.tasks import run_zwiftgopher_optimize


def _can_edit(plan: TttPlan, user) -> bool:
    """Return whether a user may edit a plan.

    Args:
        plan: The plan.
        user: The requesting user.

    Returns:
        True for the plan owner or a superuser.

    """
    return user.is_superuser or plan.created_by_id == user.id


def _render_plan_body(request: HttpRequest, plan: TttPlan, *, can_edit: bool) -> str:
    """Render the roster + results partial for a plan.

    Args:
        request: The request (for template context/CSRF).
        plan: The plan.
        can_edit: Whether edit controls should be shown.

    Returns:
        Rendered HTML string of the ``_plan_body`` partial.

    """
    result = compute_plan(plan)
    return render_to_string(
        "ttt_planner/_plan_body.html",
        {"plan": plan, "result": result, "can_edit": can_edit},
        request=request,
    )


def _plan_body_with_speed_oob(request: HttpRequest, plan: TttPlan) -> str:
    """Plan body plus an out-of-band update of the target-speed input field.

    Used by endpoints that change the target speed (Calculate / Auto-balance) so
    the input in the settings form (outside the swapped body) stays in sync.

    Args:
        request: The request.
        plan: The plan (with its new target speed already saved).

    Returns:
        Combined HTML for the swap.

    """
    body = _render_plan_body(request, plan, can_edit=True)
    oob = render_to_string("ttt_planner/_speed_input.html", {"plan": plan, "oob": True}, request=request)
    return body + oob


@login_required
@team_member_required(raise_exception=True)
@require_GET
def route_list(request: HttpRequest) -> HttpResponse:
    """List all known routes with their derived terrain type.

    Returns:
        The routes reference page.

    """
    rows = []
    for route in Route.objects.annotate(gpx_count=Count("gpx_files")).order_by("world", "name"):
        distance = float(route.distance_km)
        terrain_value = terrain.derive_terrain(distance, route.elevation_m)
        rows.append({
            "pk": route.pk,
            "name": route.name,
            "world": route.world,
            "distance_km": route.distance_km,
            "elevation_m": route.elevation_m,
            "m_per_km": round(route.elevation_m / distance, 1) if distance else 0,
            "terrain": terrain.terrain_label(terrain_value),
            "terrain_rank": terrain.TERRAIN_RANK.get(terrain_value, 0),
            "is_active": route.is_active,
            "gpx_count": route.gpx_count,
            "whatsonzwift_url": route.whatsonzwift_url,
        })
    return render(request, "ttt_planner/route_list.html", {"rows": rows})


@login_required
@team_member_required(raise_exception=True)
@require_GET
def route_detail(request: HttpRequest, route_id: int) -> HttpResponse:
    """Show a route with its uploaded GPX files and an upload form.

    Returns:
        The route detail page.

    """
    route = get_object_or_404(Route, pk=route_id)
    gpx_files = route.gpx_files.select_related("uploaded_by")
    estimated = terrain.terrain_label(terrain.derive_terrain(float(route.distance_km), route.elevation_m))
    return render(
        request,
        "ttt_planner/route_detail.html",
        {"route": route, "gpx_files": gpx_files, "estimated_terrain": estimated},
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def route_gpx_upload(request: HttpRequest, route_id: int) -> HttpResponse:
    """Upload a GPX file for a route, parsing it for distance/elevation/terrain.

    Returns:
        Redirect back to the route detail page.

    """
    route = get_object_or_404(Route, pk=route_id)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Choose a .gpx file to upload.")
        return redirect("routes:detail", route_id=route.pk)
    if not upload.name.lower().endswith(".gpx"):
        messages.error(request, "That doesn't look like a .gpx file.")
        return redirect("routes:detail", route_id=route.pk)

    content = upload.read()
    upload.seek(0)  # rewind so the FileField saves the full content
    gpx = RouteGpx(
        route=route,
        label=request.POST.get("label", "").strip(),
        notes=request.POST.get("notes", "").strip(),
        uploaded_by=request.user,
        file=upload,
    )
    try:
        stats = parse_gpx(content)
        gpx.distance_km = stats.distance_km
        gpx.elevation_m = stats.elevation_m
        gpx.terrain = stats.terrain
        gpx.point_count = stats.point_count
        gpx.profile = stats.profile
    except ValueError as exc:
        gpx.parse_error = str(exc)[:300]
        logfire.warning("GPX parse failed", route_id=route.pk, error=str(exc))
    gpx.save()

    if gpx.parse_error:
        messages.warning(request, "File saved, but it couldn't be parsed as GPX.")
    else:
        messages.success(request, f"Uploaded — {gpx.distance_km} km / {gpx.elevation_m} m ({gpx.terrain}).")
    logfire.info("Route GPX uploaded", route_id=route.pk, gpx_id=gpx.pk, user_id=request.user.id)
    return redirect("routes:detail", route_id=route.pk)


@login_required
@team_member_required(raise_exception=True)
@require_POST
def route_gpx_delete(request: HttpRequest, route_id: int, gpx_id: int) -> HttpResponse:
    """Delete a GPX file (uploader or an app admin/superuser).

    Returns:
        Redirect back to the route detail page.

    """
    gpx = get_object_or_404(RouteGpx, pk=gpx_id, route_id=route_id)
    if request.user.is_superuser or request.user.is_app_admin or gpx.uploaded_by_id == request.user.id:
        gpx.delete()
        messages.success(request, "GPX file deleted.")
    else:
        messages.error(request, "You can only delete GPX files you uploaded.")
    return redirect("routes:detail", route_id=route_id)


@login_required
@team_member_required(raise_exception=True)
@require_GET
def planner_list(request: HttpRequest) -> HttpResponse:
    """List the current user's TTT plans.

    Returns:
        The plan list page.

    """
    plans = (
        TttPlan.objects
        .filter(created_by=request.user)
        .select_related("route")
        .prefetch_related("riders")
        .annotate(rider_count=Count("riders"))
    )
    plan_rows = [{"plan": plan, "rider_count": plan.rider_count, "finish_s": quick_finish_time(plan)} for plan in plans]
    return render(
        request,
        "ttt_planner/planner_list.html",
        {"plan_rows": plan_rows, "event_types": TttPlan.EventType.choices},
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_create(request: HttpRequest) -> HttpResponse:
    """Create a new empty plan and redirect to it.

    Returns:
        Redirect to the new plan's detail page.

    """
    event_type = request.POST.get("event_type", "")
    plan = TttPlan.objects.create(
        created_by=request.user,
        name=request.POST.get("name", "").strip(),
        team_name=request.POST.get("team_name", "").strip(),
        event_type=event_type if event_type in TttPlan.EventType.values else "",
    )
    logfire.info("TTT plan created", plan_id=str(plan.pk), user_id=request.user.id)
    return redirect("ttt_planner:detail", plan_id=plan.pk)


@login_required
@team_member_required(raise_exception=True)
@require_GET
def planner_detail(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Show a plan. Read-only for non-owners (share link).

    Returns:
        The plan detail page.

    """
    plan = get_object_or_404(TttPlan.objects.select_related("route"), pk=plan_id)
    can_edit = _can_edit(plan, request.user)
    result = compute_plan(plan)
    route_options = terrain.route_options() if can_edit else []
    my_squads, other_squads = event_squads.squads_for_picker(request.user) if can_edit else ([], [])
    return render(
        request,
        "ttt_planner/planner_detail.html",
        {
            "plan": plan,
            "result": result,
            "can_edit": can_edit,
            "route_options": route_options,
            "course_types": terrain.TERRAIN_CHOICES,
            "event_types": TttPlan.EventType.choices,
            "my_squads": my_squads,
            "other_squads": other_squads,
        },
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_delete(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Delete a plan (owner only).

    Returns:
        Redirect to the plan list.

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)
    if not _can_edit(plan, request.user):
        return HttpResponse("Permission denied", status=403)
    plan.delete()
    logfire.info("TTT plan deleted", plan_id=plan_id, user_id=request.user.id)
    return redirect("ttt_planner:list")


def _get_editable_plan(request: HttpRequest, plan_id: str) -> TttPlan | None:
    """Fetch a plan if the user may edit it, else None.

    Args:
        request: The request.
        plan_id: Plan UUID.

    Returns:
        The plan, or None if not editable (caller returns 403).

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)
    return plan if _can_edit(plan, request.user) else None


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_update(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Update plan-level settings (name, team, route, target speed); recompute.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    if "name" in request.POST:
        plan.name = request.POST.get("name", "").strip()
    if "team_name" in request.POST:
        plan.team_name = request.POST.get("team_name", "").strip()
    if "event_type" in request.POST:
        event_type = request.POST.get("event_type", "")
        plan.event_type = event_type if event_type in TttPlan.EventType.values else ""
    if "target_speed_kph" in request.POST:
        with contextlib.suppress(ValueError):
            plan.target_speed_kph = max(0.0, float(request.POST.get("target_speed_kph") or 0))
    if "route" in request.POST:
        route_id = request.POST.get("route")
        plan.route = Route.objects.filter(pk=route_id).first() if route_id else None
    if "course_name" in request.POST:
        plan.course_name = request.POST.get("course_name", "").strip()
    if "course_type" in request.POST:
        course_type = request.POST.get("course_type", "")
        plan.course_type = course_type if course_type in terrain.TERRAIN_VALUES else ""
    if "cda_coef" in request.POST:
        raw = request.POST.get("cda_coef", "").strip()
        if not raw:
            plan.cda_coef = None  # fall back to global default
        else:
            with contextlib.suppress(ValueError):
                plan.cda_coef = max(0.0, float(raw))
    if "target_if" in request.POST:
        with contextlib.suppress(ValueError):
            plan.target_if = min(max(float(request.POST.get("target_if") or 0.95), 0.1), 1.5)

    plan.save()
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def draft_savings_update(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Update the per-plan draft savings from the editable position table; recompute.

    Expects ``saving`` POST values (percentages for positions 2..N, in order),
    or ``reset=1`` to clear back to the global default.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    if request.POST.get("reset"):
        plan.draft_savings = []
    else:
        # Inputs are percentages for positions 2..N; position 1 (front) is fixed at 0.
        fractions: list[float] = [0.0]
        for raw in request.POST.getlist("saving"):
            try:
                value = float(raw) / 100.0
            except TypeError, ValueError:
                value = 0.0
            fractions.append(min(max(value, 0.0), 0.95))
        plan.draft_savings = fractions

    plan.save(update_fields=["draft_savings", "updated_at"])
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


def _render_gopher_panel(request: HttpRequest, plan: TttPlan, *, can_edit: bool) -> str:
    """Render the zwiftgopher compare panel for a plan.

    Args:
        request: The request.
        plan: The plan.
        can_edit: Whether the run controls should be shown.

    Returns:
        Rendered HTML of the ``_zwiftgopher_panel`` partial.

    """
    return render_to_string(
        "ttt_planner/_zwiftgopher_panel.html",
        {
            "plan": plan,
            "result": compute_plan(plan),
            "gopher": plan.zwiftgopher_result,
            "can_edit": can_edit,
            "gopher_configured": zwiftgopher_client.is_configured(),
            "route_schedules": zwiftgopher.VALID_ROUTE_SCHEDULES,
        },
        request=request,
    )


@login_required
@team_member_required(raise_exception=True)
@require_GET
def zwiftgopher_panel(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Return the zwiftgopher panel (used for polling while a run is pending).

    Returns:
        The panel partial.

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)
    return HttpResponse(_render_gopher_panel(request, plan, can_edit=_can_edit(plan, request.user)))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def zwiftgopher_run(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Enqueue a zwiftgopher optimize run for a plan; return the panel.

    Returns:
        The panel partial (in the pending state, which self-polls).

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    schedule = request.POST.get("route_schedule", zwiftgopher.DEFAULT_ROUTE_SCHEDULE)
    if schedule not in zwiftgopher.VALID_ROUTE_SCHEDULES:
        schedule = zwiftgopher.DEFAULT_ROUTE_SCHEDULE

    plan.zwiftgopher_status = TttPlan.GopherStatus.PENDING
    plan.zwiftgopher_error = ""
    plan.zwiftgopher_request = None
    plan.zwiftgopher_raw_response = None
    plan.save(
        update_fields=["zwiftgopher_status", "zwiftgopher_error", "zwiftgopher_request", "zwiftgopher_raw_response"]
    )

    run_zwiftgopher_optimize.enqueue(str(plan.pk), schedule)
    logfire.info("zwiftgopher run enqueued", plan_id=str(plan.pk), schedule=schedule, user_id=request.user.id)
    return HttpResponse(_render_gopher_panel(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def calculate_speed(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Set the target speed to the max sustainable value for the target IF; recompute.

    Uses the current pull durations, so changing durations and recalculating
    changes the speed (and the estimated time).

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    if "target_if" in request.POST:
        with contextlib.suppress(ValueError):
            plan.target_if = min(max(float(request.POST.get("target_if") or 0.95), 0.1), 1.5)

    plan.target_speed_kph = sustainable_speed(plan)
    plan.save(update_fields=["target_if", "target_speed_kph", "updated_at"])
    return HttpResponse(_plan_body_with_speed_oob(request, plan))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def auto_balance(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Balance pull durations and order at the target IF, set the speed; recompute.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    result = compute_auto_balance(plan)
    if result is not None:
        riders_by_pk = {r.pk: r for r in plan.riders.all()}
        to_update = []
        for assignment in result.assignments:
            rider = riders_by_pk.get(assignment.rider_pk)
            if rider is None:
                continue
            rider.pull_duration_s = assignment.pull_duration_s
            rider.zero_pull = assignment.zero_pull
            rider.order = assignment.order
            to_update.append(rider)
        if to_update:
            PlanRider.objects.bulk_update(to_update, ["pull_duration_s", "zero_pull", "order"])
        plan.target_speed_kph = result.speed_kph
        plan.save(update_fields=["target_speed_kph", "updated_at"])
        logfire.info("TTT auto-balance applied", plan_id=str(plan.pk), speed=result.speed_kph)
    return HttpResponse(_plan_body_with_speed_oob(request, plan))


@login_required
@team_member_required(raise_exception=True)
@require_GET
def rider_search(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Autocomplete: search team riders to add to the plan.

    Returns:
        The search-results dropdown partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get("q", "")
    existing = set(plan.riders.exclude(zwid__isnull=True).values_list("zwid", flat=True))
    results = roster.search_riders(query, exclude_zwids=existing)
    return render(request, "ttt_planner/_rider_search.html", {"plan": plan, "results": results, "query": query.strip()})


def _next_order(plan: TttPlan) -> int:
    """Return the next pull-order index for a new rider.

    Args:
        plan: The plan.

    Returns:
        One past the current maximum order (0 if empty).

    """
    last = plan.riders.order_by("-order").first()
    return (last.order + 1) if last else 0


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_add(request: HttpRequest, plan_id: str, zwid: int) -> HttpResponse:
    """Add a team rider (by zwid) to the plan, snapshotting their data.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    if not plan.riders.filter(zwid=zwid).exists():
        data = roster.get_rider_data([zwid]).get(zwid)
        if data:
            PlanRider.objects.create(
                plan=plan,
                order=_next_order(plan),
                zwid=data.zwid,
                name=data.name,
                weight_kg=data.weight_kg,
                height_cm=data.height_cm,
                ftp_w=data.ftp_w,
            )
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_squad_add(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Add all members of a chosen event squad to the plan, snapshotting team data.

    Members with team data (ZP/ZR) get weight/height/FTP; those without are added
    name-only. Existing riders are skipped; members without a zwid can't be added.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    squad = Squad.objects.filter(pk=request.POST.get("squad")).first()
    if squad is None:
        return HttpResponse(_render_plan_body(request, plan, can_edit=True))

    users = event_squads.squad_member_users(squad)
    existing = set(plan.riders.exclude(zwid__isnull=True).values_list("zwid", flat=True))
    zwids = [u.zwid for u in users if u.zwid and u.zwid not in existing]
    data_by_zwid = roster.get_rider_data(zwids)

    order = _next_order(plan)
    for user in users:
        if not user.zwid or user.zwid in existing:
            continue
        data = data_by_zwid.get(user.zwid)
        PlanRider.objects.create(
            plan=plan,
            order=order,
            zwid=user.zwid,
            name=data.name if data else (user.get_full_name() or user.username),
            weight_kg=data.weight_kg if data else None,
            height_cm=data.height_cm if data else None,
            ftp_w=data.ftp_w if data else None,
        )
        existing.add(user.zwid)
        order += 1
    logfire.info("TTT squad added", plan_id=str(plan.pk), squad_id=squad.pk, user_id=request.user.id)
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_add_manual(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Add a manual / guest rider from typed-in fields.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    name = request.POST.get("name", "").strip()
    if name:

        def _int(key: str) -> int | None:
            raw = request.POST.get(key, "").strip()
            try:
                return int(float(raw)) if raw else None
            except ValueError:
                return None

        def _dec(key: str) -> float | None:
            raw = request.POST.get(key, "").strip()
            try:
                return float(raw) if raw else None
            except ValueError:
                return None

        PlanRider.objects.create(
            plan=plan,
            order=_next_order(plan),
            name=name,
            weight_kg=_dec("weight_kg"),
            height_cm=_int("height_cm"),
            ftp_w=_int("ftp_w"),
        )
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_remove(request: HttpRequest, plan_id: str, rider_id: int) -> HttpResponse:
    """Remove a rider from the plan.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    plan.riders.filter(pk=rider_id).delete()
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def riders_remove_selected(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Remove all riders whose ids are checked in the table.

    Expects ``rider_ids`` POST values (the checked rows).

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    rider_ids = request.POST.getlist("rider_ids")
    if rider_ids:
        plan.riders.filter(pk__in=rider_ids).delete()
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_reorder(request: HttpRequest, plan_id: str, rider_id: int, direction: str) -> HttpResponse:
    """Move a rider up or down in the pull order (swap with its neighbour).

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    riders = list(plan.riders.all())
    idx = next((i for i, r in enumerate(riders) if r.pk == rider_id), None)
    if idx is not None:
        swap = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap < len(riders):
            a, b = riders[idx], riders[swap]
            a.order, b.order = b.order, a.order
            PlanRider.objects.bulk_update([a, b], ["order"])
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def rider_update(request: HttpRequest, plan_id: str, rider_id: int) -> HttpResponse:
    """Update an editable field on a rider row (pull power/duration/zero-pull); recompute.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    rider = get_object_or_404(PlanRider, pk=rider_id, plan=plan)
    fields: list[str] = []

    if "pull_power_w" in request.POST:
        raw = request.POST.get("pull_power_w", "").strip()
        try:
            rider.pull_power_w = int(float(raw)) if raw else None
        except ValueError:
            rider.pull_power_w = None
        fields.append("pull_power_w")
    if "pull_duration_s" in request.POST:
        with contextlib.suppress(ValueError):
            rider.pull_duration_s = max(0, int(float(request.POST.get("pull_duration_s") or 0)))
        fields.append("pull_duration_s")
    if "zero_pull" in request.POST:
        rider.zero_pull = request.POST.get("zero_pull") in ("on", "true", "1")
        fields.append("zero_pull")

    if fields:
        rider.save(update_fields=fields)
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))
