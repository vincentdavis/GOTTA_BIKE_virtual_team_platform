"""Views for the TTT planner.

The main page is a server-rendered DaisyUI page; mutations are HTMX POSTs that
return the ``_plan_body`` partial (roster table + results panel), keeping the
page in sync after every edit. CSRF is supplied globally via ``hx-headers`` on
``<body>`` in base.html.
"""

from __future__ import annotations

import contextlib

import logfire
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import team_member_required
from apps.ttt_planner.models import PlanRider, Route, TttPlan
from apps.ttt_planner.services import physics, roster
from apps.ttt_planner.services.compute import auto_set_speed, compute_plan, default_draft_savings_input


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


@login_required
@team_member_required(raise_exception=True)
@require_GET
def planner_list(request: HttpRequest) -> HttpResponse:
    """List the current user's TTT plans.

    Returns:
        The plan list page.

    """
    plans = TttPlan.objects.filter(created_by=request.user).select_related("route")
    return render(
        request,
        "ttt_planner/planner_list.html",
        {"plans": plans, "default_draft_savings_input": default_draft_savings_input()},
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_create(request: HttpRequest) -> HttpResponse:
    """Create a new empty plan and redirect to it.

    Returns:
        Redirect to the new plan's detail page.

    """
    parsed = physics.parse_draft_savings(request.POST.get("draft_savings", ""))
    plan = TttPlan.objects.create(
        created_by=request.user,
        name=request.POST.get("name", "").strip(),
        team_name=request.POST.get("team_name", "").strip(),
        draft_savings=list(parsed) if parsed else [],
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
    routes = Route.objects.filter(is_active=True) if can_edit else Route.objects.none()
    return render(
        request,
        "ttt_planner/planner_detail.html",
        {"plan": plan, "result": result, "can_edit": can_edit, "routes": routes},
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
    if "target_speed_kph" in request.POST:
        with contextlib.suppress(ValueError):
            plan.target_speed_kph = max(0.0, float(request.POST.get("target_speed_kph") or 0))
    if "route" in request.POST:
        route_id = request.POST.get("route")
        plan.route = Route.objects.filter(pk=route_id).first() if route_id else None
    if "cda_coef" in request.POST:
        raw = request.POST.get("cda_coef", "").strip()
        if not raw:
            plan.cda_coef = None  # fall back to global default
        else:
            with contextlib.suppress(ValueError):
                plan.cda_coef = max(0.0, float(raw))

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


@login_required
@team_member_required(raise_exception=True)
@require_POST
def auto_speed(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Set the target speed to the suggested sustainable value; recompute.

    Returns:
        The refreshed plan body partial.

    """
    plan = _get_editable_plan(request, plan_id)
    if plan is None:
        return HttpResponse("Permission denied", status=403)

    plan.target_speed_kph = auto_set_speed(plan)
    plan.save(update_fields=["target_speed_kph", "updated_at"])
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


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
