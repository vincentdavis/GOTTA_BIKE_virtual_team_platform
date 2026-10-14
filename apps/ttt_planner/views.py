"""Views for the TTT planner.

The main page is a server-rendered DaisyUI page; mutations are HTMX POSTs that
return the ``_plan_body`` partial (roster table + results panel), keeping the
page in sync after every edit. CSRF is supplied globally via ``hx-headers`` on
``<body>`` in base.html.
"""

from __future__ import annotations

import contextlib
import math

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import (
    discord_permission_required,
    race_verified_required,
    team_member_required,
)
from apps.events import squads as event_squads
from apps.events.models import Squad
from apps.ttt_planner import terrain
from apps.ttt_planner.forms import PowerUpForm
from apps.ttt_planner.models import PlanRider, PowerUp, TttPlan
from apps.ttt_planner.services import compute, effort, physics, roster, zwiftgopher, zwiftgopher_client
from apps.ttt_planner.services.compute import (
    climb_strength,
    compute_plan,
    draft_table_for,
    optimize_plan,
    quick_finish_time,
)
from apps.ttt_planner.tasks import run_zwiftgopher_optimize
from apps.zwift_data import catalog
from apps.zwift_data.models import ZwiftDataset, ZwiftRoute, ZwiftSegment, ZwiftWorld
from apps.zwift_data.services.velo import import_velo_from_file
from apps.zwift_data.tasks import sync_zwift_data
from gotta_bike_platform import planner_access

# What Optimize does for each goal ("<speed_goal>-<pull_rule>"), shown under its button.
GOAL_SENTENCES = {
    "fastest-even": (
        "Optimize finds the fastest speed inside the limits, and sets the pull lengths and order for even effort."
    ),
    "fastest-reserve": (
        "Optimize finds the fastest speed inside the limits, and sets the pull lengths and order to keep the most W′ "
        "in reserve."
    ),
    "fastest-keep": "Optimize finds the fastest speed inside the limits for your pull lengths.",
    "hold-even": "Optimize keeps your speed, and sets the pull lengths and order for even effort.",
    "hold-reserve": "Optimize keeps your speed, and sets the pull lengths and order to keep the most W′ in reserve.",
    "hold-keep": (
        "Nothing to optimize: the speed and the pull lengths are yours. The pull schedule shows how they fit the "
        "limits."
    ),
}
# The pull-length rules as the Parameters card offers them.
PULL_RULE_HELP = {
    TttPlan.PullRule.EVEN: "Stronger riders pull longer, until everyone's IF is as even as the pull steps allow.",
    TttPlan.PullRule.RESERVE: (
        "Pulls kept short enough that every rider holds on to as much W′ as possible, for surges and the finish."
    ),
    TttPlan.PullRule.KEEP: "Optimize leaves the pull lengths and order as they are.",
}


def _render_plan_body(
    request: HttpRequest, plan: TttPlan, *, can_edit: bool, result: compute.PlanResult | None = None
) -> str:
    """Render the roster + results partial for a plan.

    Args:
        request: The request (for template context/CSRF).
        plan: The plan.
        can_edit: Whether edit controls should be shown.
        result: The plan's computed results, when the caller already has them.

    Returns:
        Rendered HTML string of the ``_plan_body`` partial.

    """
    return render_to_string(
        "ttt_planner/_plan_body.html",
        {
            "plan": plan,
            "result": result or compute_plan(plan),
            "can_edit": can_edit,
            "climb": climb_strength(plan),
        },
        request=request,
    )


@login_required
@team_member_required(raise_exception=True)
@require_GET
def route_list(request: HttpRequest) -> HttpResponse:
    """Render the reference page for the canonical Zwift dataset (worlds/routes/segments).

    Routes, worlds, and segments come from the synced Zwift Speed Lab dataset
    (:mod:`apps.zwift_data`); power-ups remain locally curated.

    Returns:
        The routes reference page.

    """
    rows = [
        {
            "name": r.name,
            "world": r.world,
            "world_id": r.world_id,
            "name_hash": r.name_hash,
            "sport": r.sport,
            "distance_km": r.distance_km,
            "ascent_m": r.ascent_m,
            "avg_gradient_pct": r.avg_gradient_pct,
            "m_per_km": round(r.ascent_m / r.distance_km, 1) if r.distance_km else 0,
            "supports_tt": r.supports_tt,
            "event_only": r.event_only,
            "level_locked": r.level_locked,
            "total_km": r.total_distance_km,
        }
        for r in ZwiftRoute.objects.all()
    ]
    segments = list(ZwiftSegment.objects.all())
    return render(
        request,
        "ttt_planner/route_list.html",
        {
            "rows": rows,
            "worlds": ZwiftWorld.objects.all(),
            "segments": segments,
            "segment_worlds": sorted({s.world for s in segments if s.world}),
            "powerups": PowerUp.objects.filter(is_active=True),
            "dataset": ZwiftDataset.get(),
            "can_check_updates": request.user.is_superuser or request.user.has_permission("racing_admin"),
        },
    )


@login_required
@team_member_required(raise_exception=True)
@require_GET
def route_detail(request: HttpRequest, name_hash: str) -> HttpResponse:
    """Show a canonical route with its elevation profile, map, and segments.

    Returns:
        The route detail page.

    """
    route = get_object_or_404(ZwiftRoute, name_hash=name_hash)
    return render(
        request,
        "ttt_planner/route_detail.html",
        {"route": route, "segments": catalog.route_segments(route.world_id, route.name_hash)},
    )


@login_required
@team_member_required(raise_exception=True)
@require_GET
def segment_detail(request: HttpRequest, segment_id: str) -> HttpResponse:
    """Show a canonical segment with its stats and the routes that cross it.

    Returns:
        The segment detail page.

    """
    segment = get_object_or_404(ZwiftSegment, segment_id=int(segment_id))
    return render(
        request,
        "ttt_planner/segment_detail.html",
        {"segment": segment, "routes": catalog.segment_routes(segment.segment_id)},
    )


@login_required
@team_member_required(raise_exception=True)
@require_GET
def route_profile_json(request: HttpRequest, world_id: int, name_hash: str) -> JsonResponse:
    """Elevation + GPS profile for one route (chart data), or 404.

    Returns:
        JSON of the profile (d/e/lat/lon arrays + summary stats).

    """
    profile = catalog.route_profile(world_id, name_hash)
    if profile is None:
        return JsonResponse({"error": "no elevation profile for this route"}, status=404)
    return JsonResponse(profile)


@login_required
@team_member_required(raise_exception=True)
@require_GET
def route_segments_json(request: HttpRequest, world_id: int, name_hash: str) -> JsonResponse:
    """Segments a route crosses, in ride order (chart bands + list).

    Returns:
        JSON ``{"segments": [...]}``.

    """
    return JsonResponse({"segments": catalog.route_segments(world_id, name_hash)})


@login_required
@discord_permission_required("racing_admin", raise_exception=True)
@require_POST
def route_check_updates(request: HttpRequest) -> HttpResponse:
    """Enqueue a background re-sync of the Zwift Speed Lab dataset (racing_admin).

    Returns:
        Redirect back to the routes page.

    """
    dataset = ZwiftDataset.get()
    if dataset.syncing:
        messages.info(request, "A data update is already in progress.")
    else:
        sync_zwift_data.enqueue()
        messages.success(request, "Checking for updates — routes & segments will refresh in the background.")
        logfire.info("zwift_data sync enqueued from routes page", user_id=request.user.id)
    return redirect("routes:list")


@login_required
@discord_permission_required("racing_admin", raise_exception=True)
@require_POST
def route_load_velo(request: HttpRequest) -> HttpResponse:
    """Import ZwiftRacing vELO2 Race weights onto canonical routes (racing_admin).

    Reads the bundled ZwiftRacing routes JSON and matches by ``name_hash``.

    Returns:
        Redirect back to the routes page.

    """
    try:
        result = import_velo_from_file()
    except (FileNotFoundError, ValueError) as exc:
        messages.error(request, f"Couldn't load vELO weights: {exc}")
        logfire.error("vELO import failed", error=str(exc), user_id=request.user.id)
        return redirect("routes:list")
    messages.success(
        request,
        f"vELO weights loaded — {result.updated} routes updated"
        + (f", {result.unmatched} unmatched." if result.unmatched else "."),
    )
    logfire.info("vELO weights imported from routes page", updated=result.updated, user_id=request.user.id)
    return redirect("routes:list")


@login_required
@race_verified_required()
def powerup_create(request: HttpRequest) -> HttpResponse:
    """Create a Zwift PowerUp (race-verified users / superusers).

    Returns:
        The PowerUp form on GET/invalid, else a redirect to the routes page.

    """
    form = PowerUpForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        powerup = form.save()
        messages.success(request, f"PowerUp “{powerup.name}” created.")
        logfire.info("PowerUp created", powerup_id=powerup.pk, user_id=request.user.id)
        return redirect("routes:list")
    return render(request, "ttt_planner/powerup_form.html", {"form": form, "mode": "create"})


@login_required
@race_verified_required()
def powerup_edit(request: HttpRequest, powerup_id: int) -> HttpResponse:
    """Edit a Zwift PowerUp (race-verified users / superusers).

    Returns:
        The PowerUp form on GET/invalid, else a redirect to the routes page.

    """
    powerup = get_object_or_404(PowerUp, pk=powerup_id)
    form = PowerUpForm(request.POST or None, request.FILES or None, instance=powerup)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "PowerUp updated.")
        logfire.info("PowerUp updated", powerup_id=powerup.pk, user_id=request.user.id)
        return redirect("routes:list")
    return render(request, "ttt_planner/powerup_form.html", {"form": form, "mode": "edit", "powerup": powerup})


@login_required
@team_member_required(raise_exception=True)
@require_GET
def planner_list(request: HttpRequest) -> HttpResponse:
    """List TTT plans: the current user's first, then other members'.

    Returns:
        The plan list page.

    """
    all_plans = (
        TttPlan.objects
        .select_related("route", "created_by")
        .prefetch_related("riders")
        .annotate(rider_count=Count("riders"))
    )
    plan_rows = [
        {"plan": plan, "rider_count": plan.rider_count, "finish_s": quick_finish_time(plan)}
        for plan in all_plans.filter(created_by=request.user)
    ]
    other_plan_rows = [
        {"plan": plan, "rider_count": plan.rider_count, "finish_s": quick_finish_time(plan)}
        for plan in all_plans.exclude(created_by=request.user)
    ]
    return render(
        request,
        "ttt_planner/planner_list.html",
        {
            "plan_rows": plan_rows,
            "other_plan_rows": other_plan_rows,
            "event_types": TttPlan.EventType.choices,
        },
    )


@login_required
@team_member_required(raise_exception=True)
@require_GET
def planner_help(request: HttpRequest) -> HttpResponse:
    """Explain how the TTT planner works: its model, the numbers it shows, and its buttons.

    Every constant the page quotes is read live (Constance and ``services/compute.py``),
    so those cannot drift. The prose describing the method lives in
    ``ttt_planner/help.html`` and has to be kept in step with ``services/compute.py``.

    Returns:
        The help page.

    """
    params = physics.params_from_constance()
    example_kph, example_kg, example_cm = 40.0, compute.FALLBACK_WEIGHT_KG, compute.FALLBACK_HEIGHT_CM
    third_wheel = physics.draft_savings_fraction(3, params)
    # The W′ worked example, computed by the model itself so its numbers cannot drift.
    cp, w_prime, front, draft, pull_s, sit_s = 300, 20_000, 380, 240, 60, 180
    low_j = effort.lowest_w_prime_bal(front, draft, pull_s, pull_s + sit_s, cp, w_prime)
    w_example = {
        "cp": cp,
        "w_prime_kj": f"{w_prime / 1000:g}",
        "front": front,
        "draft": draft,
        "pull_s": pull_s,
        "sit_min": f"{sit_s / 60:g}",
        "spent_kj": f"{(front - cp) * pull_s / 1000:.1f}",
        "refill_pct": round(100 * (1 - math.exp(-(cp - draft) * sit_s / w_prime))),
        "low_kj": f"{low_j / 1000:.1f}",
        "low_pct": round(100 * low_j / w_prime),
    }
    return render(
        request,
        "ttt_planner/help.html",
        {
            "params": params,
            "std_cda_coef": physics.params_from_constance(cda_coef_key="STD_CDA_COEF").cda_coef,
            "draft_rows": [
                {"position": i + 1, "saving_pct": round(fraction * 100, 1)}
                for i, fraction in enumerate(params.draft_savings)
            ],
            "fallback_weight_kg": compute.FALLBACK_WEIGHT_KG,
            "fallback_height_cm": compute.FALLBACK_HEIGHT_CM,
            # A new plan's settings, read off the model so the page cannot drift from them.
            "defaults": {
                name: TttPlan._meta.get_field(name).default
                for name in ("target_if", "w_prime_reserve_pct", "min_pull_s", "max_pull_s", "pull_step_s")
            },
            "pull_steps": compute.PULL_STEPS_S,
            "fallback_w_prime_kj": f"{compute.FALLBACK_W_PRIME_J / 1000:g}",
            "w_example": w_example,
            "climb_grade_pct": f"{compute.TTT_CLIMB_GRADE * 100:g}",
            "example": {
                "kph": f"{example_kph:g}",
                "kg": f"{example_kg:g}",
                "cm": f"{example_cm:g}",
                "cda": physics.estimate_cda(example_cm, example_kg, params),
                "front_w": round(
                    physics.power_for_speed(example_kph, weight_kg=example_kg, height_cm=example_cm, params=params)
                ),
                "third_wheel_pct": f"{third_wheel * 100:g}",
                "third_wheel_w": round(
                    physics.power_for_speed(
                        example_kph,
                        weight_kg=example_kg,
                        height_cm=example_cm,
                        draft_factor=1.0 - third_wheel,
                        params=params,
                    )
                ),
            },
        },
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
    """Show a plan: editable for its creator and edit squad, read-only for anyone else until they confirm.

    Returns:
        The plan detail page.

    """
    plan = get_object_or_404(
        TttPlan.objects.select_related("route", "edit_squad__event", "created_by", "updated_by"), pk=plan_id
    )
    can_manage = planner_access.can_manage(plan, request.user)
    can_edit = can_manage or planner_access.edit_requested(request)
    if can_edit and not can_manage:
        logfire.info("TTT plan opened for editing by a non-owner", plan_id=str(plan.pk), user_id=request.user.id)
    result = compute_plan(plan)
    route_options = terrain.route_options() if can_edit else []
    my_squads, other_squads = event_squads.squads_for_picker(request.user) if can_edit else ([], [])
    # Whether the currently-selected edit squad appears in the active-event picker.
    # If not (its event has ended), the template renders it as a standalone option
    # so a settings save doesn't silently clear the grant.
    picker_pks = {s["pk"] for s in my_squads} | {s["pk"] for s in other_squads}
    return render(
        request,
        "ttt_planner/planner_detail.html",
        {
            "plan": plan,
            "result": result,
            "can_edit": can_edit,
            "can_manage": can_manage,
            "route_options": route_options,
            "course_types": terrain.TERRAIN_CHOICES,
            "event_types": TttPlan.EventType.choices,
            "my_squads": my_squads,
            "other_squads": other_squads,
            "edit_squad_active": plan.edit_squad_id in picker_pks,
            "pull_steps": compute.PULL_STEPS_S,
            "pull_rules": [
                {"value": rule.value, "label": rule.label, "help": PULL_RULE_HELP[rule]} for rule in TttPlan.PullRule
            ],
            "goal_sentence": GOAL_SENTENCES[f"{plan.speed_goal}-{plan.pull_rule}"],
            "goal_sentences": GOAL_SENTENCES,
        },
    )


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_delete(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Delete a plan: its creator, edit squad or a superuser only, never any team member.

    Returns:
        Redirect to the plan list.

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)
    if not planner_access.can_manage(plan, request.user):
        logfire.warning("Refused a TTT plan delete by a non-owner", plan_id=plan_id, user_id=request.user.id)
        return HttpResponse("Permission denied", status=403)
    plan.delete()
    logfire.info("TTT plan deleted", plan_id=plan_id, user_id=request.user.id)
    return redirect("ttt_planner:list")


def _clamp(value: int, ceiling: int) -> int:
    """Clamp an integer into the non-negative range the caller allows.

    This endpoint writes straight to the model without ``full_clean``, so the
    ``PlanRider`` field validators never run here — the clamp is what keeps an
    out-of-range value from reaching the column (a negative violates the positive-int
    CHECK constraint, and anything over smallint raises DataError on PostgreSQL while
    SQLite stores it happily, i.e. a 500 that only reproduces in production).

    Args:
        value: The parsed input value.
        ceiling: The maximum to allow.

    Returns:
        The value bounded to ``[0, ceiling]``.

    """
    return min(max(value, 0), ceiling)


def _changing(request: HttpRequest, plan_id: str) -> TttPlan:
    """Fetch a plan whose contents this request changes, recording the user as its last editor.

    Any team member may change a plan's contents (``gotta_bike_platform.planner_access``);
    deleting it and choosing its edit squad are checked separately, with ``can_manage``.

    Args:
        request: The request.
        plan_id: Plan UUID.

    Returns:
        The plan.

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)
    planner_access.record_editor(plan, request.user)
    return plan


@login_required
@team_member_required(raise_exception=True)
@require_POST
def plan_update(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Update plan-level settings (name, team, route, target speed); recompute.

    Any team member may; choosing the edit squad is refused, before anything is saved,
    unless the user can manage the plan.

    Returns:
        The refreshed plan body partial.

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)
    if "edit_squad" in request.POST and not planner_access.can_manage(plan, request.user):
        logfire.warning("Refused a TTT edit-squad change by a non-owner", plan_id=str(plan.pk), user_id=request.user.id)
        return HttpResponse("Permission denied", status=403)

    if "name" in request.POST:
        plan.name = request.POST.get("name", "").strip()
    if "team_name" in request.POST:
        plan.team_name = request.POST.get("team_name", "").strip()
    if "event_type" in request.POST:
        event_type = request.POST.get("event_type", "")
        plan.event_type = event_type if event_type in TttPlan.EventType.values else ""
    if "route" in request.POST:
        route_id = request.POST.get("route")
        plan.route = ZwiftRoute.objects.filter(pk=route_id).first() if route_id else None
    if "course_name" in request.POST:
        plan.course_name = request.POST.get("course_name", "").strip()
    if "course_type" in request.POST:
        course_type = request.POST.get("course_type", "")
        plan.course_type = course_type if course_type in terrain.TERRAIN_VALUES else ""
    _take_parameters(request, plan)
    if "edit_squad" in request.POST:
        squad_id = request.POST.get("edit_squad")
        plan.edit_squad = Squad.objects.filter(pk=squad_id).first() if squad_id else None

    plan.updated_by = request.user
    plan.save()
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))


def _take_parameters(request: HttpRequest, plan: TttPlan) -> None:
    """Read the Parameters card from a post: the goal, the speed, the limits and the CdA coefficient.

    Shared by the card's own saves and by Optimize, which sends the whole card with the click
    so a value typed a moment ago counts even if its own save has not landed. A typed speed
    that differs from the plan's is a speed to hold, so it switches the goal to holding it.

    Args:
        request: The post.
        plan: The plan, changed in memory (the caller saves it).

    """
    if request.POST.get("speed_goal") in TttPlan.SpeedGoal.values:
        plan.speed_goal = request.POST["speed_goal"]
    if request.POST.get("pull_rule") in TttPlan.PullRule.values:
        plan.pull_rule = request.POST["pull_rule"]
    if "target_speed_kph" in request.POST:
        with contextlib.suppress(ValueError):
            speed = round(max(0.0, float(request.POST.get("target_speed_kph") or 0)), 1)
            if speed != round(float(plan.target_speed_kph), 1):
                plan.speed_goal = TttPlan.SpeedGoal.HOLD
            plan.target_speed_kph = speed
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
    _take_balance_settings(request, plan)


def _take_balance_settings(request: HttpRequest, plan: TttPlan) -> None:
    """Read the W′ reserve and Optimize's pull limits from a settings post.

    They are stored as :class:`compute.Limits` cleans them, so what the form shows after a
    save is exactly what Optimize uses: a step from ``PULL_STEPS_S``, the pulls on its grid
    and in order, the reserve between 0 and 90%.

    Args:
        request: The settings post.
        plan: The plan, changed in memory (the caller saves it).

    """

    def number(key: str) -> int | None:
        raw = (request.POST.get(key) or "").strip()
        try:
            return int(float(raw)) if raw else None
        except ValueError:
            return None

    if (reserve := number("w_prime_reserve_pct")) is not None:
        plan.w_prime_reserve_pct = min(max(reserve, 0), 90)
    for key in ("pull_step_s", "min_pull_s", "max_pull_s"):
        if (value := number(key)) is not None:
            setattr(plan, key, max(value, 0))
    limits = compute.Limits.for_plan(plan)
    plan.pull_step_s, plan.min_pull_s, plan.max_pull_s = limits.step_s, limits.min_pull_s, limits.max_pull_s


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
    plan = _changing(request, plan_id)

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
    body = _render_plan_body(request, plan, can_edit=True)
    if request.POST.get("reset"):
        # The inputs live in the Parameters card, outside the swapped body, so a reset sends
        # them back out of band. Not after an ordinary edit: replacing the inputs then would
        # take focus away from the cell the rider is typing in.
        body += render_to_string(
            "ttt_planner/_draft_savings.html",
            {"plan": plan, "draft_table": draft_table_for(plan), "can_edit": True, "oob": True},
            request=request,
        )
    return HttpResponse(body)


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
    # The page asks for the panel with ?edit=1 while its edit controls show (and the panel's own polling keeps it).
    can_edit = planner_access.can_manage(plan, request.user) or planner_access.edit_requested(request)
    return HttpResponse(_render_gopher_panel(request, plan, can_edit=can_edit))


@login_required
@team_member_required(raise_exception=True)
@require_POST
def zwiftgopher_run(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Enqueue a zwiftgopher optimize run for a plan; return the panel.

    Returns:
        The panel partial (in the pending state, which self-polls).

    """
    plan = _changing(request, plan_id)

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
def optimize(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Carry out the plan's goal: set the speed, and the pull lengths and order when it says to; recompute.

    The click sends the whole Parameters card, so the goal and limits on screen are what
    count. The response swaps the plan body, sends the speed field back out of band, and
    says what happened in the card's live region.

    Returns:
        The refreshed plan body partial, with the out-of-band updates.

    """
    plan = _changing(request, plan_id)
    _take_parameters(request, plan)
    plan.save()

    outcome = optimize_plan(plan)
    if outcome is not None:
        if outcome.assignments is not None:
            riders_by_pk = {r.pk: r for r in plan.riders.all()}
            to_update = []
            for assignment in outcome.assignments:
                rider = riders_by_pk.get(assignment.rider_pk)
                if rider is None:
                    continue
                rider.pull_duration_s = assignment.pull_duration_s
                rider.zero_pull = assignment.zero_pull
                rider.order = assignment.order
                to_update.append(rider)
            if to_update:
                PlanRider.objects.bulk_update(to_update, ["pull_duration_s", "zero_pull", "order"])
        plan.target_speed_kph = outcome.speed_kph
        plan.save(update_fields=["target_speed_kph", "updated_at"])
    logfire.info(
        "TTT plan optimized",
        plan_id=str(plan.pk),
        user_id=request.user.id,
        speed_goal=plan.speed_goal,
        pull_rule=plan.pull_rule,
        applied=outcome is not None,
        speed=float(plan.target_speed_kph),
        relaxed=bool(outcome and outcome.relaxed),
    )

    result = compute_plan(plan)
    body = _render_plan_body(request, plan, can_edit=True, result=result)
    oob = render_to_string("ttt_planner/_speed_input.html", {"plan": plan, "oob": True}, request=request)
    message = render_to_string(
        "ttt_planner/_optimize_message.html",
        {"plan": plan, "outcome": outcome, "result": result, "oob": True},
        request=request,
    )
    return HttpResponse(body + oob + message)


@login_required
@team_member_required(raise_exception=True)
@require_GET
def rider_search(request: HttpRequest, plan_id: str) -> HttpResponse:
    """Autocomplete: search team riders to add to the plan.

    Returns:
        The search-results dropdown partial.

    """
    plan = get_object_or_404(TttPlan, pk=plan_id)

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
    plan = _changing(request, plan_id)

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
                cp_w=data.cp_w,
                w_prime_j=data.w_prime_j,
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
    plan = _changing(request, plan_id)

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
            cp_w=data.cp_w if data else None,
            w_prime_j=data.w_prime_j if data else None,
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
    plan = _changing(request, plan_id)

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
    plan = _changing(request, plan_id)

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
    plan = _changing(request, plan_id)

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
    plan = _changing(request, plan_id)

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
    plan = _changing(request, plan_id)

    rider = get_object_or_404(PlanRider, pk=rider_id, plan=plan)
    fields: list[str] = []

    if "pull_power_w" in request.POST:
        raw = request.POST.get("pull_power_w", "").strip()
        try:
            rider.pull_power_w = _clamp(int(float(raw)), PlanRider.MAX_PULL_POWER_W) if raw else None
        except ValueError:
            rider.pull_power_w = None
        fields.append("pull_power_w")
    if "pull_duration_s" in request.POST:
        with contextlib.suppress(ValueError):
            rider.pull_duration_s = _clamp(
                int(float(request.POST.get("pull_duration_s") or 0)), PlanRider.MAX_PULL_DURATION_S
            )
        fields.append("pull_duration_s")
    # An unchecked checkbox is not submitted at all, so keying off "is zero_pull in
    # POST?" alone can only ever turn this on. The marker (sent via hx-vals on the
    # checkbox) says "this request is about zero_pull", which lets an absent value
    # mean False without the pull power/duration updates clobbering the flag.
    if "zero_pull_submitted" in request.POST or "zero_pull" in request.POST:
        rider.zero_pull = request.POST.get("zero_pull") in ("on", "true", "1")
        fields.append("zero_pull")

    if fields:
        rider.save(update_fields=fields)
    return HttpResponse(_render_plan_body(request, plan, can_edit=True))
