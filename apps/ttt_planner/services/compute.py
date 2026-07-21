"""Compute TTT plan results (pull power, IF, averages, finish time) from riders."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from apps.ttt_planner.services import physics

if TYPE_CHECKING:
    from apps.ttt_planner.models import PlanRider, TttPlan

# Defaults used when a rider has no weight/height on file, so the model still runs.
FALLBACK_WEIGHT_KG = 75.0
FALLBACK_HEIGHT_CM = 178.0


@dataclass
class RiderResult:
    """Computed metrics for a single rider on the plan."""

    rider: PlanRider
    weight_kg: float
    height_cm: float
    cda: float
    pull_power_w: int
    pull_wkg: float
    avg_power_w: int
    intensity_factor: float | None
    missing_data: bool
    normalized_power_w: int | None = None
    tss: float | None = None


@dataclass
class DraftRow:
    """A single row of the draft-savings-by-position table."""

    position: int
    saving_pct: float


@dataclass
class PlanResult:
    """Computed metrics for the whole plan."""

    riders: list[RiderResult]
    target_speed_kph: float
    avg_team_power_w: int
    estimated_time_s: float
    draft_table: list[DraftRow]
    draft_savings_input: str
    cda_coef: float


def params_for_plan(plan: TttPlan) -> physics.PhysicsParams:
    """Build physics params for a plan, applying its per-plan draft savings.

    Starts from the Constance-derived defaults and overrides ``draft_savings``
    with the plan's own list when one is set.

    Args:
        plan: The plan.

    Returns:
        The resolved :class:`~apps.ttt_planner.services.physics.PhysicsParams`.

    """
    params = physics.params_from_constance()
    parsed = physics.parse_draft_savings(plan.draft_savings)
    if parsed:
        params = replace(params, draft_savings=parsed)
    if plan.cda_coef is not None:
        params = replace(params, cda_coef=plan.cda_coef)
    return params


def _draft_table(params: physics.PhysicsParams) -> list[DraftRow]:
    """Render the draft-savings-by-position table from physics params.

    Args:
        params: Resolved physics params.

    Returns:
        One :class:`DraftRow` per configured wheel position.

    """
    return [DraftRow(position=i + 1, saving_pct=round(frac * 100, 1)) for i, frac in enumerate(params.draft_savings)]


def format_draft_savings_input(params: physics.PhysicsParams) -> str:
    """Format the resolved savings as a comma-separated percentage string.

    Used to pre-fill the per-plan editing input.

    Args:
        params: Resolved physics params.

    Returns:
        e.g. ``"0, 23.3, 30, 36.6"``.

    """
    return ", ".join(f"{round(frac * 100, 1):g}" for frac in params.draft_savings)


def quick_finish_time(plan: TttPlan, params: physics.PhysicsParams | None = None) -> float:
    """Estimate a plan's finish time cheaply (no NP simulation), e.g. for lists.

    Args:
        plan: The plan (its ``riders`` should be prefetched for efficiency).
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        Estimated finish time in seconds (0.0 if no route).

    """
    if not plan.route:
        return 0.0
    if params is None:
        params = params_for_plan(plan)

    riders = list(plan.riders.all())
    if riders:
        avg_weight = sum(float(r.weight_kg or FALLBACK_WEIGHT_KG) for r in riders) / len(riders)
        avg_height = sum(float(r.height_cm or FALLBACK_HEIGHT_CM) for r in riders) / len(riders)
    else:
        avg_weight, avg_height = FALLBACK_WEIGHT_KG, FALLBACK_HEIGHT_CM

    return physics.estimate_time_seconds(
        float(plan.route.distance_km),
        plan.route.ascent_m,
        float(plan.target_speed_kph),
        avg_weight_kg=avg_weight,
        avg_height_cm=avg_height,
        params=params,
    )


_MAX_NP_SECONDS = 14400  # cap the simulated series at 4 hours


def _add_np_and_tss(
    results: list[RiderResult],
    series_inputs: list[tuple[float, float, bool, int]],
    *,
    cycle_s: int,
    duration_s: float,
) -> None:
    """Fill in each rider's Normalized Power and TSS for the race duration.

    Builds a 1-second power series for each rider over the estimated race time --
    front power during their pulls, draft power while sitting in -- phased so each
    rider's first pull starts after the riders ahead of them. Then derives NP and
    TSS. Mutates ``results`` in place.

    Args:
        results: Per-rider results to annotate (same order as ``series_inputs``).
        series_inputs: (front_power, draft_power, zero_pull, pull_duration) per rider.
        cycle_s: Total rotation length in seconds.
        duration_s: Estimated race duration in seconds.

    """
    total = min(round(duration_s), _MAX_NP_SECONDS)
    if total <= 0:
        return

    # Pull start-offset for each rider (cumulative pull time of riders ahead).
    offsets: list[int | None] = []
    offset = 0
    for _front, _draft, zero_pull, dur in series_inputs:
        if zero_pull or dur <= 0:
            offsets.append(None)
        else:
            offsets.append(offset)
            offset += dur

    for idx, rr in enumerate(results):
        front_power, draft_power, zero_pull, dur = series_inputs[idx]
        off = offsets[idx]
        if zero_pull or dur <= 0 or off is None:
            np_value = draft_power  # constant draft -> NP equals that power
        else:
            series = [front_power if ((t - off) % cycle_s) < dur else draft_power for t in range(total)]
            np_value = physics.normalized_power(series)

        ftp = float(rr.rider.ftp_w) if rr.rider.ftp_w else None
        rr.normalized_power_w = round(np_value)
        tss = physics.training_stress_score(np_value, ftp, total)
        rr.tss = round(tss, 1) if tss is not None else None


def _grade(plan: TttPlan) -> float:
    """Return the average gradient for the plan's route.

    Args:
        plan: The plan.

    Returns:
        Average gradient as a fraction (0.0 if no route).

    """
    if not plan.route:
        return 0.0
    return physics.average_grade(float(plan.route.distance_km), plan.route.ascent_m)


def compute_plan(plan: TttPlan, params: physics.PhysicsParams | None = None) -> PlanResult:
    """Compute pull power, IF, averages, and finish time for a plan.

    The pull power for each rider is the power to hold the target speed on the
    front (no draft). Average power blends the front pull with drafting during the
    rest of the rotation. IF is based on the demanding pull effort vs FTP.

    Args:
        plan: The plan to compute, with its ``riders`` available.
        params: Physics constants; falls back to Constance-derived params.

    Returns:
        A populated :class:`PlanResult`.

    """
    if params is None:
        params = params_for_plan(plan)

    target = float(plan.target_speed_kph)
    grade = _grade(plan)
    riders = list(plan.riders.all())

    pulling = [r for r in riders if not r.zero_pull]
    team_size = len(riders)
    # Mean draft savings while sitting in (positions 2..N), used for avg power.
    if team_size > 1:
        mean_savings = sum(physics.draft_savings_fraction(p, params) for p in range(2, team_size + 1)) / (team_size - 1)
    else:
        mean_savings = 0.0
    draft_factor = max(0.0, 1.0 - mean_savings)

    cycle_s = sum(r.pull_duration_s for r in pulling) or 1

    results: list[RiderResult] = []
    # Per-rider (front_power, draft_power, zero_pull, pull_duration) for the NP pass.
    series_inputs: list[tuple[float, float, bool, int]] = []
    power_sum = 0.0
    for r in riders:
        missing = r.weight_kg is None or r.height_cm is None
        weight = float(r.weight_kg) if r.weight_kg is not None else FALLBACK_WEIGHT_KG
        height = float(r.height_cm) if r.height_cm is not None else FALLBACK_HEIGHT_CM
        cda = physics.estimate_cda(height, weight, params)

        if r.pull_power_w:
            front_power = float(r.pull_power_w)
        else:
            front_power = physics.power_for_speed(
                target, weight_kg=weight, height_cm=height, grade=grade, draft_factor=1.0, params=params
            )

        draft_power = physics.power_for_speed(
            target, weight_kg=weight, height_cm=height, grade=grade, draft_factor=draft_factor, params=params
        )

        if r.zero_pull:
            avg_power = draft_power
            pull_display = 0
        else:
            own_pull = r.pull_duration_s
            avg_power = (front_power * own_pull + draft_power * (cycle_s - own_pull)) / cycle_s
            pull_display = round(front_power)

        ftp = float(r.ftp_w) if r.ftp_w else None
        if_value = (
            physics.intensity_factor(front_power, ftp)
            if not r.zero_pull
            else physics.intensity_factor(draft_power, ftp)
        )

        results.append(
            RiderResult(
                rider=r,
                weight_kg=weight,
                height_cm=height,
                cda=round(cda, 3),
                pull_power_w=pull_display,
                pull_wkg=round(pull_display / weight, 2) if weight else 0.0,
                avg_power_w=round(avg_power),
                intensity_factor=round(if_value, 2) if if_value is not None else None,
                missing_data=missing,
            )
        )
        series_inputs.append((front_power, draft_power, r.zero_pull, r.pull_duration_s))
        power_sum += avg_power

    avg_team_power = round(power_sum / team_size) if team_size else 0

    avg_weight = sum(rr.weight_kg for rr in results) / len(results) if results else FALLBACK_WEIGHT_KG
    avg_height = sum(rr.height_cm for rr in results) / len(results) if results else FALLBACK_HEIGHT_CM
    estimated_time = 0.0
    if plan.route:
        estimated_time = physics.estimate_time_seconds(
            float(plan.route.distance_km),
            plan.route.ascent_m,
            target,
            avg_weight_kg=avg_weight,
            avg_height_cm=avg_height,
            params=params,
        )

    # Normalized Power + TSS need a finish time (i.e. a chosen route).
    if estimated_time > 0:
        _add_np_and_tss(results, series_inputs, cycle_s=cycle_s, duration_s=estimated_time)

    return PlanResult(
        riders=results,
        target_speed_kph=target,
        avg_team_power_w=avg_team_power,
        estimated_time_s=estimated_time,
        draft_table=_draft_table(params),
        draft_savings_input=format_draft_savings_input(params),
        cda_coef=params.cda_coef,
    )


def _mean_draft_factor(team_size: int, params: physics.PhysicsParams) -> float:
    """Average sitting-in draft factor (1 - mean savings over positions 2..N).

    Args:
        team_size: Number of riders in the rotation.
        params: Physics constants.

    Returns:
        The aerodynamic multiplier applied while drafting.

    """
    if team_size > 1:
        mean_savings = sum(physics.draft_savings_fraction(p, params) for p in range(2, team_size + 1)) / (team_size - 1)
    else:
        mean_savings = 0.0
    return max(0.0, 1.0 - mean_savings)


def _rider_wh(rider: PlanRider) -> tuple[float, float]:
    """Return a rider's weight/height, substituting fallbacks when missing.

    Args:
        rider: The plan rider.

    Returns:
        ``(weight_kg, height_cm)``.

    """
    weight = float(rider.weight_kg) if rider.weight_kg is not None else FALLBACK_WEIGHT_KG
    height = float(rider.height_cm) if rider.height_cm is not None else FALLBACK_HEIGHT_CM
    return weight, height


def sustainable_speed(
    plan: TttPlan, *, target_if: float | None = None, params: physics.PhysicsParams | None = None
) -> float:
    """Solve for the fastest team speed every rider can sustain at the target IF.

    For each rider, their cycle-average power (front power during their own pull +
    draft power while sitting in, weighted by pull duration) must stay at or below
    ``target_if * FTP``. The binding (weakest-relative) rider caps the speed.
    Pull durations therefore directly affect the result.

    Args:
        plan: The plan.
        target_if: Intensity factor target; defaults to the plan's ``target_if``.
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        The sustainable speed in km/h (rounded to 0.1), or the current target
        speed if it cannot be derived (no FTP data).

    """
    if params is None:
        params = params_for_plan(plan)
    if target_if is None:
        target_if = float(plan.target_if or 0.95)

    all_riders = list(plan.riders.all())
    ftp_riders = [r for r in all_riders if r.ftp_w]
    if not ftp_riders:
        return float(plan.target_speed_kph)

    grade = _grade(plan)
    draft_factor = _mean_draft_factor(len(all_riders), params)
    cycle = sum(r.pull_duration_s for r in all_riders if not r.zero_pull) or 1

    def avg_power(rider: PlanRider, speed: float) -> float:
        weight, height = _rider_wh(rider)
        front = physics.power_for_speed(
            speed, weight_kg=weight, height_cm=height, grade=grade, draft_factor=1.0, params=params
        )
        draft = physics.power_for_speed(
            speed, weight_kg=weight, height_cm=height, grade=grade, draft_factor=draft_factor, params=params
        )
        pull = 0 if rider.zero_pull else rider.pull_duration_s
        return (front * pull + draft * (cycle - pull)) / cycle

    def feasible(speed: float) -> bool:
        return all(avg_power(r, speed) <= target_if * r.ftp_w for r in ftp_riders)

    lo, hi = 0.0, 120.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if feasible(mid):
            lo = mid
        else:
            hi = mid
    return round(lo, 1)


# Auto-balance tuning.
_BALANCE_MAX_PULL_S = 180
_BALANCE_MIN_PULL_S = 10
_BALANCE_STEP_S = 5


@dataclass
class BalanceAssignment:
    """A proposed pull assignment for one rider from auto-balance."""

    rider_pk: int
    pull_duration_s: int
    zero_pull: bool
    order: int


@dataclass
class BalanceResult:
    """Result of an auto-balance: per-rider assignments plus the team speed."""

    speed_kph: float
    assignments: list[BalanceAssignment]


def compute_auto_balance(
    plan: TttPlan, *, target_if: float | None = None, params: physics.PhysicsParams | None = None
) -> BalanceResult | None:
    """Balance pull durations so every rider is equally stressed at the target IF.

    Finds the speed at which the riders' required front-time fractions sum to one
    (exactly one rider on the front at all times, each at ``target_if * FTP``),
    then turns those fractions into pull durations (stronger riders pull longer),
    orders the rotation strongest-first, and benches riders who cannot even hold
    the draft at that speed (zero-pull).

    Args:
        plan: The plan.
        target_if: Intensity factor target; defaults to the plan's ``target_if``.
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        A :class:`BalanceResult`, or None if there are no riders with FTP.

    """
    if params is None:
        params = params_for_plan(plan)
    if target_if is None:
        target_if = float(plan.target_if or 0.95)

    all_riders = list(plan.riders.all())
    ftp_riders = [r for r in all_riders if r.ftp_w]
    if not ftp_riders:
        return None

    grade = _grade(plan)
    draft_factor = _mean_draft_factor(len(all_riders), params)

    def powers(rider: PlanRider, speed: float) -> tuple[float, float]:
        weight, height = _rider_wh(rider)
        front = physics.power_for_speed(
            speed, weight_kg=weight, height_cm=height, grade=grade, draft_factor=1.0, params=params
        )
        draft = physics.power_for_speed(
            speed, weight_kg=weight, height_cm=height, grade=grade, draft_factor=draft_factor, params=params
        )
        return front, draft

    def front_share(rider: PlanRider, speed: float) -> float:
        front, draft = powers(rider, speed)
        budget = target_if * rider.ftp_w
        if front <= draft:
            return 1.0
        return min(max((budget - draft) / (front - draft), 0.0), 1.0)

    # Largest speed where the sum of required front-time shares is still >= 1.
    lo, hi = 0.0, 120.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sum(front_share(r, mid) for r in ftp_riders) >= 1.0:
            lo = mid
        else:
            hi = mid
    speed = lo

    shares = {r.pk: front_share(r, speed) for r in ftp_riders}
    pullers = [r for r in ftp_riders if shares[r.pk] > 1e-3]
    benched = [r for r in ftp_riders if shares[r.pk] <= 1e-3]

    assignments: list[BalanceAssignment] = []
    order = 0
    if pullers:
        max_share = max(shares[r.pk] for r in pullers)
        cycle = _BALANCE_MAX_PULL_S / max_share if max_share > 0 else _BALANCE_MAX_PULL_S
        durations = {
            r.pk: max(round(shares[r.pk] * cycle / _BALANCE_STEP_S) * _BALANCE_STEP_S, _BALANCE_MIN_PULL_S)
            for r in pullers
        }
        for rider in sorted(pullers, key=lambda r: -durations[r.pk]):
            assignments.append(BalanceAssignment(rider.pk, durations[rider.pk], False, order))
            order += 1

    for rider in benched:
        assignments.append(BalanceAssignment(rider.pk, rider.pull_duration_s, True, order))
        order += 1
    # Riders without FTP can't be balanced; keep their settings, ordered last.
    for rider in (r for r in all_riders if not r.ftp_w):
        assignments.append(BalanceAssignment(rider.pk, rider.pull_duration_s, rider.zero_pull, order))
        order += 1

    return BalanceResult(speed_kph=round(speed, 1), assignments=assignments)


# ----- Climb compare (per-rider climb-strength heatmap) ------------------------------------------

from apps.ttt_planner.services import climb as climb_engine  # noqa: E402

# Per-rider climb heatmap axes: a single reference grade, swept over climb lengths.
TTT_CLIMB_GRADE = 0.08
TTT_CLIMB_LENGTHS_M: list[float] = [500, 1000, 2000, 4000, 8000, 15000]
_TTT_CURVE_FIELDS = (
    (5, "power_w5"), (15, "power_w15"), (30, "power_w30"), (60, "power_w60"),
    (120, "power_w120"), (300, "power_w300"), (1200, "power_w1200"),
)


def _ttt_format_length(metres: float) -> str:
    """Format a climb length for column headers.

    Args:
        metres: Length in metres.

    Returns:
        ``"500 m"`` or ``"2 km"`` style.

    """
    return f"{int(metres)} m" if metres < 1000 else f"{metres / 1000:g} km"


def _ttt_format_gap(seconds: float) -> str:
    """Format a rider's time gap behind the squad's fastest climber.

    Args:
        seconds: Seconds behind the fastest (>= 0).

    Returns:
        ``"0"`` when level, else ``"+12s"`` / ``"+1:20"``.

    """
    if seconds < 0.5:
        return "0"
    minutes, secs = divmod(round(seconds), 60)
    return f"+{minutes}:{secs:02d}" if seconds >= 60 else f"+{round(seconds)}s"


def _ttt_gap_rgb(t: float) -> str:
    """Green (with the group) -> amber -> red (dropped) for a normalized gap.

    Args:
        t: Normalized gap in [0, 1] (0 = fastest, 1 = biggest gap in the grid).

    Returns:
        A ``"rgb(r,g,b)"`` string.

    """
    green, amber, red = (34, 197, 94), (250, 204, 21), (239, 68, 68)
    t = max(0.0, min(1.0, t))
    lo, hi, k = (green, amber, t / 0.5) if t <= 0.5 else (amber, red, (t - 0.5) / 0.5)
    rgb = tuple(round(lo[i] + (hi[i] - lo[i]) * k) for i in range(3))
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _ttt_climb_rider(plan_rider: PlanRider) -> climb_engine.ClimbRider | None:
    """Build a climb-engine rider from a plan rider's linked ZR power curve.

    Args:
        plan_rider: The plan rider (needs a ``zwid`` linked to a ``ZRRider``).

    Returns:
        A ``ClimbRider``, or None if there's no zwid, ZR record, weight, or curve.

    """
    if not plan_rider.zwid:
        return None
    from apps.zwiftracing.models import ZRRider

    zr = ZRRider.objects.filter(zwid=plan_rider.zwid).first()
    if zr is None:
        return None
    curve = {secs: float(v) for secs, field in _TTT_CURVE_FIELDS if (v := getattr(zr, field))}
    weight = float(zr.weight) if zr.weight else (float(plan_rider.weight_kg) if plan_rider.weight_kg else 0.0)
    height = float(zr.height or plan_rider.height_cm or 0)
    if not weight or not curve:
        return None
    return climb_engine.ClimbRider(name=plan_rider.name, weight_kg=weight, height_cm=height, power_curve=curve)


def climb_strength(plan: TttPlan) -> dict:
    """Per-rider climb heatmap: each rider's time gap behind the fastest climber.

    Rows are riders, columns are climb lengths at ``TTT_CLIMB_GRADE``; each cell is
    how far behind the squad's fastest climber the rider would finish that climb
    (0 = sets the pace, larger = first to get dropped).

    Args:
        plan: The plan.

    Returns:
        ``{"available": False}`` if fewer than two riders have ZR power + weight,
        otherwise ``available`` plus ``grade_pct``, ``lengths`` (column headers),
        and ``rows`` (one per rider with colored gap cells).

    """
    riders = [cr for r in plan.riders.all() if (cr := _ttt_climb_rider(r))]
    if len(riders) < 2:
        return {"available": False}

    params = physics.params_from_constance(cda_coef_key="STD_CDA_COEF")
    if plan.cda_coef is not None:
        params = replace(params, cda_coef=plan.cda_coef)

    # times[rider_index][length] = climb seconds (or None).
    times: list[dict[float, float | None]] = []
    for rider in riders:
        row = {}
        for length in TTT_CLIMB_LENGTHS_M:
            res = climb_engine.climb_effort(rider, length, TTT_CLIMB_GRADE, params=params)
            row[length] = res.time_s if res else None
        times.append(row)

    fastest = {
        length: min((t for row in times if (t := row[length]) is not None), default=None)
        for length in TTT_CLIMB_LENGTHS_M
    }
    max_gap = max(
        (row[length] - fastest[length] for row in times for length in TTT_CLIMB_LENGTHS_M
         if row[length] is not None and fastest[length] is not None),
        default=0,
    ) or 1

    rows = []
    for rider, row in zip(riders, times, strict=True):
        cells = []
        for length in TTT_CLIMB_LENGTHS_M:
            t, f = row[length], fastest[length]
            if t is None or f is None:
                cells.append({"label": "—", "rgb": ""})
                continue
            gap = t - f
            cells.append({"gap_s": gap, "label": _ttt_format_gap(gap), "rgb": _ttt_gap_rgb(gap / max_gap)})
        rows.append({"name": rider.name, "cells": cells})

    return {
        "available": True,
        "grade_pct": f"{TTT_CLIMB_GRADE * 100:g}",
        "lengths": [_ttt_format_length(m) for m in TTT_CLIMB_LENGTHS_M],
        "rows": rows,
    }
