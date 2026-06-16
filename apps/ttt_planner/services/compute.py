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
        plan.route.elevation_m,
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
    return physics.average_grade(float(plan.route.distance_km), plan.route.elevation_m)


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
            plan.route.elevation_m,
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


def auto_set_speed(plan: TttPlan, params: physics.PhysicsParams | None = None) -> float:
    """Suggest a target flat speed where the average puller's IF is ~0.95.

    Uses the mean weight/height/FTP of pulling riders to find a sustainable
    front-pull speed.

    Args:
        plan: The plan.
        params: Physics constants; falls back to Constance-derived params.

    Returns:
        Suggested speed in km/h (rounded to 0.1), or the current speed if it
        cannot be derived.

    """
    if params is None:
        params = params_for_plan(plan)

    pulling = [r for r in plan.riders.all() if not r.zero_pull and r.ftp_w]
    if not pulling:
        return float(plan.target_speed_kph)

    avg_weight = sum(float(r.weight_kg or FALLBACK_WEIGHT_KG) for r in pulling) / len(pulling)
    avg_height = sum(float(r.height_cm or FALLBACK_HEIGHT_CM) for r in pulling) / len(pulling)
    avg_ftp = sum(r.ftp_w for r in pulling) / len(pulling)

    target_power = 0.95 * avg_ftp
    grade = _grade(plan)
    speed = physics.speed_for_power(
        target_power, weight_kg=avg_weight, height_cm=avg_height, grade=grade, params=params
    )
    return round(speed, 1)
