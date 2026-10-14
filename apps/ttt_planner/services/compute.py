"""Compute TTT plan results (pull power, load, W′ balance, finish time) from riders.

The target speed is a **flat** speed: every power here (pull, sitting in, and the limits
Optimize works to) is the power to hold it on level road. The route's climbing enters once,
in the finish-time estimate (``physics.estimate_time_seconds``), which turns that flat-speed
effort into a speed on the course.

Through a rotation, a rider's power is a square wave: their pull power on the front, their
sitting-in power for the rest. ``services/effort.py`` turns that into Normalized Power (so
IF and TSS) and the lowest W′ balance. Every rider with an FTP is held to two limits: IF
(NP / FTP) at or under Target IF, and W′bal at or above the plan's W′ reserve. That includes
riders who take no pulls: they still have to hold the wheel, so a plan never goes faster by
leaving a rider to be dropped.

A plan's goal has two halves, stored on the plan: how fast (the fastest the limits allow, or
hold the target speed) and how the pull lengths are set (for even effort, for the most W′
in reserve, or left alone). :func:`optimize_plan` carries it out; :class:`PlanStatus` says
whether the plan as it stands fits the limits, and who stops it going faster.
``ttt_planner/help.html`` explains all of this to riders, so keep it in step with this module.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

from apps.ttt_planner.services import course, effort, physics

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.ttt_planner.models import PlanRider, TttPlan

# Defaults used when a rider has no weight/height on file, so the model still runs.
FALLBACK_WEIGHT_KG = 75.0
FALLBACK_HEIGHT_CM = 178.0
# W′ for a rider without a Zwift Racing value, close to the team's average (about 19.5 kJ).
FALLBACK_W_PRIME_J = 20_000
# Zwift Racing reports a W′ of 0 for some riders; anything this small counts as missing.
MIN_CREDIBLE_W_PRIME_J = 1_000
# Allowed pull steps for Optimize, in seconds.
PULL_STEPS_S = (5, 10, 15, 30)
# A plan's goal, as ``TttPlan.SpeedGoal`` and ``TttPlan.PullRule`` store it.
FASTEST, HOLD = "fastest", "hold"
EVEN, RESERVE, KEEP = "even", "reserve", "keep"
# Slack for float noise when checking the IF and W′ limits.
_IF_TOLERANCE = 1e-6
_W_PRIME_TOLERANCE_J = 1e-3
# Bisection on speed (km/h): the search range and the number of halvings.
_SPEED_RANGE_KPH = (0.0, 120.0)
_CALCULATE_STEPS = 40
_BALANCE_STEPS = 16


@dataclass(frozen=True)
class RiderSpec:
    """What the model needs to know about one plan rider, read once.

    ``cp`` falls back to FTP and ``w_prime`` to :data:`FALLBACK_W_PRIME_J` when the rider
    has no Zwift Racing value; the ``*_estimated`` flags say so.
    """

    pk: int
    weight_kg: float
    height_cm: float
    missing_body: bool
    ftp: float | None
    cp: float | None
    w_prime: float | None
    cp_estimated: bool
    w_prime_estimated: bool
    pull_s: int
    zero_pull: bool
    pull_power_override: float | None

    @property
    def pulls(self) -> bool:
        """Whether the rider takes pulls."""
        return not self.zero_pull and self.pull_s > 0


def rider_spec(rider: PlanRider) -> RiderSpec:
    """Read what the model needs from a plan rider, filling the documented fallbacks.

    Args:
        rider: The plan rider.

    Returns:
        The rider's :class:`RiderSpec`.

    """
    ftp = float(rider.ftp_w) if rider.ftp_w else None
    cp = float(rider.cp_w) if rider.cp_w else ftp
    w_prime_known = bool(rider.w_prime_j) and rider.w_prime_j >= MIN_CREDIBLE_W_PRIME_J
    fallback = float(FALLBACK_W_PRIME_J) if cp else None
    w_prime = float(rider.w_prime_j) if w_prime_known else fallback
    return RiderSpec(
        pk=rider.pk,
        weight_kg=float(rider.weight_kg) if rider.weight_kg is not None else FALLBACK_WEIGHT_KG,
        height_cm=float(rider.height_cm) if rider.height_cm is not None else FALLBACK_HEIGHT_CM,
        missing_body=rider.weight_kg is None or rider.height_cm is None,
        ftp=ftp,
        cp=cp,
        w_prime=w_prime,
        cp_estimated=cp is not None and not rider.cp_w,
        w_prime_estimated=w_prime is not None and not w_prime_known,
        pull_s=int(rider.pull_duration_s or 0),
        zero_pull=rider.zero_pull,
        pull_power_override=float(rider.pull_power_w) if rider.pull_power_w else None,
    )


@dataclass(frozen=True)
class Limits:
    """A plan's balancing settings, cleaned up so the optimisers can trust them."""

    target_if: float
    reserve: float  # share of W′ every puller must keep, 0-0.9
    min_pull_s: int
    max_pull_s: int
    step_s: int

    @classmethod
    def for_plan(cls, plan: TttPlan, target_if: float | None = None) -> Limits:
        """Read a plan's limits: the step from :data:`PULL_STEPS_S`, pulls on its grid.

        Args:
            plan: The plan.
            target_if: Overrides the plan's Target IF.

        Returns:
            The plan's :class:`Limits`.

        """
        from apps.ttt_planner.models import PlanRider

        step = plan.pull_step_s if plan.pull_step_s in PULL_STEPS_S else 15
        ceiling = PlanRider.MAX_PULL_DURATION_S - PlanRider.MAX_PULL_DURATION_S % step

        def on_grid(seconds: int | None, default: int) -> int:
            value = default if seconds is None else seconds
            return min(max(step, round(value / step) * step), ceiling)

        shortest = on_grid(plan.min_pull_s, 30)
        reserve_pct = 25 if plan.w_prime_reserve_pct is None else plan.w_prime_reserve_pct
        return cls(
            target_if=float(target_if if target_if is not None else (plan.target_if or 0.95)),
            reserve=min(max(reserve_pct / 100.0, 0.0), 0.9),
            min_pull_s=shortest,
            max_pull_s=max(on_grid(plan.max_pull_s, 180), shortest),
            step_s=step,
        )

    def pull_grid(self) -> list[int]:
        """Return every pull length Optimize may hand out.

        Returns:
            The lengths from the shortest to the longest pull, one step apart.

        """
        return list(range(self.min_pull_s, self.max_pull_s + 1, self.step_s))


@dataclass(frozen=True)
class Load:
    """One rider's load through the race at one speed."""

    front_w: float
    draft_w: float
    avg_w: float
    np_w: float
    # Lowest W′ balance in joules: None without a CP, -inf when a long race drains it.
    w_prime_low_j: float | None


def rider_load(spec: RiderSpec, front: float, draft: float, cycle_s: float, race_s: float | None) -> Load:
    """Work out a rider's load from their powers and the rotation.

    Args:
        spec: The rider.
        front: Their power on the front, in watts.
        draft: Their power sitting in, in watts.
        cycle_s: Length of the rotation, in seconds.
        race_s: Race length in seconds, or ``None`` (no route): then W′ is judged for a
            long race, the worst case.

    Returns:
        The rider's :class:`Load`.

    """
    has_w_prime = spec.cp is not None and spec.w_prime is not None
    if spec.pulls and cycle_s > 0:
        pull = min(spec.pull_s, cycle_s)
        avg = (front * pull + draft * (cycle_s - pull)) / cycle_s
        np_w = effort.cycle_np(front, draft, pull, cycle_s)
        low = None
        if has_w_prime:
            pulls = math.ceil(race_s / cycle_s) if race_s else None
            low = effort.lowest_w_prime_bal(front, draft, pull, cycle_s, spec.cp, spec.w_prime, pulls)
    else:
        avg = np_w = draft
        low = effort.sitting_w_prime_bal(draft, spec.cp, spec.w_prime, race_s) if has_w_prime else None
    return Load(front_w=front, draft_w=draft, avg_w=avg, np_w=np_w, w_prime_low_j=low)


def over_if(spec: RiderSpec, load: Load, limits: Limits) -> bool:
    """Whether a rider's IF (NP / FTP) is above Target IF.

    Args:
        spec: The rider.
        load: Their load.
        limits: The plan's limits.

    Returns:
        True when over; False without an FTP, which cannot be judged.

    """
    return spec.ftp is not None and load.np_w / spec.ftp > limits.target_if + _IF_TOLERANCE


def below_reserve(spec: RiderSpec, load: Load, limits: Limits) -> bool:
    """Whether a rider's W′ balance dips under the plan's W′ reserve.

    Args:
        spec: The rider.
        load: Their load.
        limits: The plan's limits.

    Returns:
        True when it dips under; False without a W′ to judge.

    """
    if load.w_prime_low_j is None or spec.w_prime is None:
        return False
    return load.w_prime_low_j < limits.reserve * spec.w_prime - _W_PRIME_TOLERANCE_J


def within_limits(spec: RiderSpec, load: Load, limits: Limits) -> bool:
    """Whether a rider's load is inside both limits.

    Args:
        spec: The rider.
        load: Their load.
        limits: The plan's limits.

    Returns:
        True unless IF is over Target IF or W′bal dips under the reserve.

    """
    return not over_if(spec, load, limits) and not below_reserve(spec, load, limits)


@dataclass(frozen=True)
class _Powers:
    """Power to hold a speed on level road, on the front and sitting in."""

    params: physics.PhysicsParams
    draft_factor: float

    def front(self, spec: RiderSpec, speed: float) -> float:
        if spec.pull_power_override is not None:
            return spec.pull_power_override
        return physics.power_for_speed(
            speed, weight_kg=spec.weight_kg, height_cm=spec.height_cm, draft_factor=1.0, params=self.params
        )

    def draft(self, spec: RiderSpec, speed: float) -> float:
        return physics.power_for_speed(
            speed,
            weight_kg=spec.weight_kg,
            height_cm=spec.height_cm,
            draft_factor=self.draft_factor,
            params=self.params,
        )


@dataclass
class RiderResult:
    """Computed metrics for a single rider on the plan.

    ``pull_pct_ftp`` is how hard the pull is (pull power as a percentage of FTP; pulls
    usually sit above 100). ``intensity_factor`` is the standard IF, NP / FTP, which Target
    IF caps for every rider who pulls. ``w_prime_low_pct`` is the lowest W′ balance over
    the race, as a percentage of W′ (below zero when the rider runs out).
    """

    rider: PlanRider
    weight_kg: float
    height_cm: float
    cda: float
    pull_power_w: int
    pull_wkg: float
    avg_power_w: int
    pull_pct_ftp: int | None
    missing_data: bool
    normalized_power_w: int | None = None
    intensity_factor: float | None = None
    tss: float | None = None
    cp_w: int | None = None
    w_prime_kj: float | None = None
    cp_estimated: bool = False
    w_prime_estimated: bool = False
    w_prime_low_pct: int | None = None
    w_prime_empty: bool = False
    over_if: bool = False
    below_reserve: bool = False

    @property
    def over_target(self) -> bool:
        """Whether the rider is past either limit at this speed."""
        return self.over_if or self.below_reserve


@dataclass
class DraftRow:
    """A single row of the draft-savings-by-position table."""

    position: int
    saving_pct: float


@dataclass(frozen=True)
class Holdback:
    """A rider who would cross a limit if the team went any faster with these pulls."""

    name: str
    limit: str  # "if" (IF over Target IF) or "w_prime" (W′ under the reserve)


@dataclass
class PlanStatus:
    """Whether the plan as it stands fits its limits, and what stops it going faster.

    ``fastest_kph`` is the fastest flat speed the current pulls allow, to 0.1 km/h, and
    ``held_back_by`` the riders who would cross a limit 0.1 km/h above it. Both are None or
    empty when nobody can be judged: no rider has an FTP, or nobody pulls.
    """

    speed_kph: float
    judged: bool
    anyone_pulls: bool
    over: list[RiderResult]
    fastest_kph: float | None
    held_back_by: list[Holdback]

    @property
    def fits(self) -> bool:
        """Whether every rider is inside both limits at the plan's speed."""
        return not self.over

    @property
    def at_fastest(self) -> bool:
        """Whether the plan already rides at the fastest speed its pulls allow."""
        return self.fastest_kph is not None and abs(self.fastest_kph - self.speed_kph) < 0.05


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
    # The distance raced over the estimated time: the target flat speed once the route's
    # climbs and descents are applied (0.0 without a route).
    course_avg_kph: float = 0.0
    # The distance raced: the profile's, lead-in included, or the route's own without a profile.
    course_km: float = 0.0
    leadin_km: float = 0.0
    # What the course chart draws (see _course_chart), or None without a route profile.
    course_chart: dict | None = None
    # Whether any rider is computed on the fallback weight/height below.
    any_estimated: bool = False
    # Whether any rider's CP or W′ is a fallback rather than a Zwift Racing value.
    any_power_estimated: bool = False
    target_if: float = 0.95
    w_prime_reserve_pct: int = 25
    status: PlanStatus | None = None
    fallback_weight_kg: ClassVar[float] = FALLBACK_WEIGHT_KG
    fallback_height_cm: ClassVar[float] = FALLBACK_HEIGHT_CM
    fallback_w_prime_kj: ClassVar[float] = FALLBACK_W_PRIME_J / 1000

    @property
    def highest_if(self) -> float | None:
        """The highest IF over the riders, or None when nobody has an FTP."""
        values = [r.intensity_factor for r in self.riders if r.intensity_factor is not None]
        return max(values) if values else None

    @property
    def lowest_w_prime_pct(self) -> int | None:
        """The lowest W′ balance over the riders, as a percentage of W′; None when none can be judged."""
        values = [r.w_prime_low_pct for r in self.riders if r.w_prime_low_pct is not None]
        return min(values) if values else None

    @property
    def anyone_runs_out(self) -> bool:
        """Whether some rider's W′ runs out over the race."""
        return any(r.w_prime_empty for r in self.riders)


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


def draft_table_for(plan: TttPlan) -> list[DraftRow]:
    """Return the plan's draft-savings-by-position table (its own list, or the site default).

    Args:
        plan: The plan.

    Returns:
        One :class:`DraftRow` per configured wheel position.

    """
    return _draft_table(params_for_plan(plan))


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
    """Estimate a plan's finish time cheaply, e.g. for lists.

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
    specs = [rider_spec(r) for r in plan.riders.all()]
    return _race_seconds(plan, specs, float(plan.target_speed_kph), params) or 0.0


# Marks a course nobody has looked up yet, as distinct from None (no route profile).
_UNSET = object()


def _team_body(specs: list[RiderSpec]) -> tuple[float, float]:
    """Return the team's average weight and height: the typical rider who stands in for the team.

    Args:
        specs: The riders.

    Returns:
        ``(weight_kg, height_cm)``, the fallbacks for a plan with no riders.

    """
    if not specs:
        return FALLBACK_WEIGHT_KG, FALLBACK_HEIGHT_CM
    return sum(s.weight_kg for s in specs) / len(specs), sum(s.height_cm for s in specs) / len(specs)


def course_for(plan: TttPlan, specs: list[RiderSpec], params: physics.PhysicsParams) -> course.Course | None:
    """Return the plan's route ready to ride at a steady effort.

    Args:
        plan: The plan.
        specs: Its riders (their average weight and height stand in for the team).
        params: Physics constants.

    Returns:
        A :class:`course.Course`, or None without a route or without a profile for it.

    """
    if not plan.route:
        return None
    profile = course.load_profile(plan.route)
    if profile is None:
        return None
    weight, height = _team_body(specs)
    return course.Course.build(profile, weight, height, params)


def _race_seconds(
    plan: TttPlan,
    specs: list[RiderSpec],
    speed: float,
    params: physics.PhysicsParams,
    route_course: course.Course | object | None = _UNSET,
) -> float | None:
    """Return the estimated race length at a target flat speed, or None without a route.

    With the route's profile, the team rides it at a steady effort, lead-in included
    (:meth:`course.Course.seconds`). Without one, it rides the route's distance on its
    average gradient (``physics.estimate_time_seconds``).

    Args:
        plan: The plan.
        specs: Its riders (their average weight and height stand in for the team).
        speed: Target flat speed, in km/h.
        params: Physics constants.
        route_course: The plan's course from :func:`course_for`, when the caller already has
            it; looked up when left out.

    Returns:
        Race seconds, or None.

    """
    if not plan.route or speed <= 0:
        return None
    if route_course is _UNSET:
        route_course = course_for(plan, specs, params)
    if isinstance(route_course, course.Course):
        seconds = route_course.seconds(speed)
    else:
        weight, height = _team_body(specs)
        seconds = physics.estimate_time_seconds(
            float(plan.route.distance_km),
            plan.route.ascent_m,
            speed,
            avg_weight_kg=weight,
            avg_height_cm=height,
            params=params,
        )
    return seconds if 0 < seconds < math.inf else None


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


def compute_plan(plan: TttPlan, params: physics.PhysicsParams | None = None) -> PlanResult:
    """Compute pull power, load, W′ balance, and finish time for a plan.

    Args:
        plan: The plan to compute, with its ``riders`` available.
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        A populated :class:`PlanResult`.

    """
    if params is None:
        params = params_for_plan(plan)
    limits = Limits.for_plan(plan)
    target = float(plan.target_speed_kph)
    riders = list(plan.riders.all())
    specs = [rider_spec(r) for r in riders]
    powers = _Powers(params, _mean_draft_factor(len(specs), params))
    cycle = sum(s.pull_s for s in specs if s.pulls)
    route_course = course_for(plan, specs, params)
    race_s = _race_seconds(plan, specs, target, params, route_course)

    results: list[RiderResult] = []
    for rider, spec in zip(riders, specs, strict=True):
        load = rider_load(spec, powers.front(spec, target), powers.draft(spec, target), cycle, race_s)
        pull_w = round(load.front_w) if spec.pulls else 0
        intensity = load.np_w / spec.ftp if spec.ftp else None
        tss = physics.training_stress_score(load.np_w, spec.ftp, race_s) if race_s else None
        low = load.w_prime_low_j
        low_finite = low is not None and math.isfinite(low)
        results.append(
            RiderResult(
                rider=rider,
                weight_kg=spec.weight_kg,
                height_cm=spec.height_cm,
                cda=round(physics.estimate_cda(spec.height_cm, spec.weight_kg, params), 3),
                pull_power_w=pull_w,
                pull_wkg=round(pull_w / spec.weight_kg, 2) if spec.weight_kg else 0.0,
                avg_power_w=round(load.avg_w),
                pull_pct_ftp=round(load.front_w / spec.ftp * 100) if spec.pulls and spec.ftp else None,
                missing_data=spec.missing_body,
                normalized_power_w=round(load.np_w),
                intensity_factor=round(intensity, 2) if intensity is not None else None,
                tss=round(tss, 1) if tss is not None else None,
                cp_w=round(spec.cp) if spec.cp else None,
                w_prime_kj=round(spec.w_prime / 1000, 1) if spec.w_prime else None,
                cp_estimated=spec.cp_estimated,
                w_prime_estimated=spec.w_prime_estimated,
                w_prime_low_pct=round(100 * low / spec.w_prime) if low_finite and spec.w_prime else None,
                w_prime_empty=low is not None and low < 0,
                over_if=over_if(spec, load, limits),
                below_reserve=below_reserve(spec, load, limits),
            )
        )

    estimated_time = race_s or 0.0
    judged = any(s.ftp for s in specs)
    anyone_pulls = any(s.pulls for s in specs)
    fastest = _fastest(plan, specs, limits, params, route_course)
    if route_course is not None:
        course_km = route_course.profile.length_m / 1000
        leadin_km = route_course.profile.leadin_m / 1000
    else:
        course_km = float(plan.route.distance_km) if plan.route else 0.0
        leadin_km = 0.0
    return PlanResult(
        riders=results,
        target_speed_kph=target,
        avg_team_power_w=round(sum(r.avg_power_w for r in results) / len(results)) if results else 0,
        estimated_time_s=estimated_time,
        draft_table=_draft_table(params),
        draft_savings_input=format_draft_savings_input(params),
        cda_coef=params.cda_coef,
        course_avg_kph=round(course_km / estimated_time * 3600, 1) if estimated_time > 0 else 0.0,
        course_km=round(course_km, 2),
        leadin_km=round(leadin_km, 2),
        course_chart=(
            _course_chart(route_course, target, specs, powers)
            if route_course is not None and specs and target > 0
            else None
        ),
        any_estimated=any(s.missing_body for s in specs),
        any_power_estimated=any(s.cp_estimated or s.w_prime_estimated for s in specs),
        target_if=limits.target_if,
        w_prime_reserve_pct=round(limits.reserve * 100),
        status=PlanStatus(
            speed_kph=target,
            judged=judged,
            anyone_pulls=anyone_pulls,
            over=[r for r in results if r.over_target],
            fastest_kph=fastest,
            held_back_by=(
                _held_back_by(plan, riders, specs, limits, params, round(fastest + 0.1, 1), route_course)
                if fastest
                else []
            ),
        ),
    )


def _fastest(
    plan: TttPlan,
    specs: list[RiderSpec],
    limits: Limits,
    params: physics.PhysicsParams,
    route_course: course.Course | object | None = _UNSET,
) -> float | None:
    """Return the fastest flat speed (to 0.1 km/h, rounded down) at which every rider is within the limits.

    Args:
        plan: The plan (for its route).
        specs: Its riders, with the pull lengths to judge.
        limits: Target IF and W′ reserve.
        params: Physics constants.
        route_course: The plan's course from :func:`course_for`; looked up when left out.

    Returns:
        The speed in km/h, or None when nobody pulls, nobody has an FTP, or no speed fits.

    """
    judged = [s for s in specs if s.ftp]
    if not judged or not any(s.pulls for s in specs):
        return None
    powers = _Powers(params, _mean_draft_factor(len(specs), params))
    cycle = sum(s.pull_s for s in specs if s.pulls)
    if route_course is _UNSET:
        route_course = course_for(plan, specs, params)

    def fits(speed: float) -> bool:
        race_s = _race_seconds(plan, specs, speed, params, route_course)
        for spec in judged:
            load = rider_load(spec, powers.front(spec, speed), powers.draft(spec, speed), cycle, race_s)
            if not within_limits(spec, load, limits):
                return False
        return True

    lo, hi = _SPEED_RANGE_KPH
    for _ in range(_CALCULATE_STEPS):
        mid = (lo + hi) / 2.0
        if fits(mid):
            lo = mid
        else:
            hi = mid
    return math.floor(lo * 10) / 10 if lo >= 0.1 else None


def _max_speed(
    plan: TttPlan,
    specs: list[RiderSpec],
    limits: Limits,
    params: physics.PhysicsParams,
    *,
    fallback: float,
    route_course: course.Course | object | None = _UNSET,
) -> float:
    """Return :func:`_fastest`, or ``fallback`` when it cannot say.

    Args:
        plan: The plan (for its route).
        specs: Its riders, with the pull lengths to judge.
        limits: Target IF and W′ reserve.
        params: Physics constants.
        fallback: Returned when nobody pulls, nobody has an FTP, or no speed fits.
        route_course: The plan's course from :func:`course_for`; looked up when left out.

    Returns:
        The speed in km/h.

    """
    fastest = _fastest(plan, specs, limits, params, route_course)
    return fallback if fastest is None else fastest


def _held_back_by(
    plan: TttPlan,
    riders: list[PlanRider],
    specs: list[RiderSpec],
    limits: Limits,
    params: physics.PhysicsParams,
    speed: float,
    route_course: course.Course | object | None = _UNSET,
) -> list[Holdback]:
    """Name the riders who would be past a limit at ``speed``, and which limit.

    Called with a speed just above the fastest the pulls allow, so it names who sets that speed.

    Args:
        plan: The plan (for its route).
        riders: Its riders.
        specs: The same riders, as the model reads them.
        limits: Target IF and W′ reserve.
        params: Physics constants.
        speed: The flat speed to judge, in km/h.
        route_course: The plan's course from :func:`course_for`; looked up when left out.

    Returns:
        One :class:`Holdback` per rider past a limit, in pull order; IF is named when both are.

    """
    powers = _Powers(params, _mean_draft_factor(len(specs), params))
    cycle = sum(s.pull_s for s in specs if s.pulls)
    race_s = _race_seconds(plan, specs, speed, params, route_course)
    held: list[Holdback] = []
    for rider, spec in zip(riders, specs, strict=True):
        if not spec.ftp:
            continue
        load = rider_load(spec, powers.front(spec, speed), powers.draft(spec, speed), cycle, race_s)
        if over_if(spec, load, limits):
            held.append(Holdback(rider.name, "if"))
        elif below_reserve(spec, load, limits):
            held.append(Holdback(rider.name, "w_prime"))
    return held


def _course_chart(route_course: course.Course, flat_kph: float, specs: list[RiderSpec], powers: _Powers) -> dict:
    """Lay out what the course chart draws: the profile, the team's speed, and every rider's powers.

    At each chart point the team holds the typical rider's steady power, which sets its
    speed on that slope; each rider's power to hold that speed on the front (no draft) and
    sitting in (the plan's average draft) follows. A custom pull power scales a rider's pulls
    in proportion to the model's own on level road. Riders come in plan order and without
    names: the page renders the names itself.

    Args:
        route_course: The plan's course.
        flat_kph: The target flat speed.
        specs: The plan's riders, as the model reads them, in plan order.
        powers: The plan's pulling and sitting-in powers on level road.

    Returns:
        A JSON-ready dict: distances (km), elevations (m), gradients (%) and team speeds (km/h)
        at each point, and per rider their weight, FTP, whether they pull, and their powers there.

    """
    profile, params = route_course.profile, route_course.params
    points = course.chart_samples(route_course)
    last = len(route_course.grades) - 1
    grades = [route_course.grades[min(i, last)] for i in points]
    power = route_course.team_power(flat_kph)
    speeds = [route_course.speed(power, grade) for grade in grades]

    def watts(spec: RiderSpec, draft_factor: float, scale: float = 1.0) -> list[int]:
        return [
            round(
                scale
                * physics.power_for_speed(
                    kph,
                    weight_kg=spec.weight_kg,
                    height_cm=spec.height_cm,
                    grade=grade,
                    draft_factor=draft_factor,
                    params=params,
                )
            )
            for grade, kph in zip(grades, speeds, strict=True)
        ]

    rows = []
    for spec in specs:
        scale = 1.0
        if spec.pull_power_override is not None:
            level = physics.power_for_speed(flat_kph, weight_kg=spec.weight_kg, height_cm=spec.height_cm, params=params)
            scale = spec.pull_power_override / level if level > 0 else 1.0
        rows.append({
            "kg": round(spec.weight_kg, 1),
            "ftp": round(spec.ftp) if spec.ftp else None,
            "pulls": spec.pulls,
            "front": watts(spec, 1.0, scale),
            "sit": watts(spec, powers.draft_factor),
        })
    return {
        "flat_kph": flat_kph,
        "km": [round(profile.d[i] / 1000, 3) for i in points],
        "elev": [round(profile.e[i], 1) for i in points],
        "grade": [round(grade * 100, 1) for grade in grades],
        "kph": [round(kph, 1) for kph in speeds],
        "leadin_km": round(profile.leadin_m / 1000, 3),
        "riders": rows,
    }


def sustainable_speed(
    plan: TttPlan, *, target_if: float | None = None, params: physics.PhysicsParams | None = None
) -> float:
    """Solve for the fastest flat speed at which every rider stays within the limits, keeping the pulls.

    Keeps the pull lengths as they are. Every rider with an FTP must keep IF (NP / FTP) at
    or under Target IF and their W′ balance at or above the plan's W′ reserve, riders who
    take no pulls too; the first rider to reach a limit sets the speed. This is Optimize
    with "Keep my pull lengths" and "As fast as the limits allow".

    Args:
        plan: The plan.
        target_if: Intensity factor target; defaults to the plan's ``target_if``.
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        The speed in km/h, rounded down to 0.1, or the current target speed when it
        cannot be derived (nobody pulls, or nobody has an FTP).

    """
    if params is None:
        params = params_for_plan(plan)
    specs = [rider_spec(r) for r in plan.riders.all()]
    return _max_speed(plan, specs, Limits.for_plan(plan, target_if), params, fallback=float(plan.target_speed_kph))


# ----- Optimize: carry out the plan's goal ------------------------------------------------------


@dataclass
class BalanceAssignment:
    """A proposed pull assignment for one rider from Optimize."""

    rider_pk: int
    pull_duration_s: int
    zero_pull: bool
    order: int


@dataclass
class OptimizeResult:
    """What Optimize settled on for a plan."""

    speed_kph: float
    # New pull lengths and order for every rider, or None when the pulls stay as they are.
    assignments: list[BalanceAssignment] | None
    # Holding a speed only: it is too fast for the rule's other limit, so that limit was set aside.
    relaxed: bool = False


@dataclass(frozen=True)
class _Try:
    """One rider on one pull length in one rotation, judged for a long race (the worst case for W′)."""

    pull_s: int  # 0: sits in
    intensity: float  # IF, NP / FTP
    w_left: float  # lowest W′ balance as a share of W′; -inf when the race empties it
    over_if: bool
    below_reserve: bool


@dataclass(frozen=True)
class _Rule:
    """How Optimize compares pull lengths: the worst rider on ``score`` decides, lower is better.

    ``then`` breaks ties the same way, and every pull must be ``allowed``.
    """

    score: Callable[[_Try], float]
    then: Callable[[_Try], float]
    allowed: Callable[[_Try], bool]


def _rule(pull_rule: str, *, hold_if: bool, hold_w_prime: bool) -> _Rule:
    """Build the comparison for a pull rule.

    Even effort keeps the highest IF lowest; Most W′ in reserve keeps the lowest W′ balance
    highest. Each breaks ties on the other's measure.

    Args:
        pull_rule: :data:`EVEN` or :data:`RESERVE`.
        hold_if: Whether every pull must keep IF at or under Target IF.
        hold_w_prime: Whether every pull must keep W′ at or above the reserve.

    Returns:
        The :class:`_Rule`.

    """

    def allowed(t: _Try) -> bool:
        return not (hold_if and t.over_if) and not (hold_w_prime and t.below_reserve)

    def intensity(t: _Try) -> float:
        return t.intensity

    def w_spent(t: _Try) -> float:
        return -t.w_left

    if pull_rule == RESERVE:
        return _Rule(score=w_spent, then=intensity, allowed=allowed)
    return _Rule(score=intensity, then=w_spent, allowed=allowed)


def _tries(spec: RiderSpec, front: float, draft: float, cycle_s: int, lengths: list[int], limits: Limits) -> list[_Try]:
    """Judge a rider sitting in, then on each pull length, in a rotation of ``cycle_s``.

    Args:
        spec: The rider (with an FTP).
        front: Their power on the front.
        draft: Their power sitting in.
        cycle_s: The rotation length.
        lengths: The pull lengths to try, shortest first.
        limits: Target IF and W′ reserve.

    Returns:
        One :class:`_Try` for sitting in, then one per length, in that order.

    """
    tries = []
    for pull in (0, *lengths):
        trial = replace(spec, pull_s=pull, zero_pull=pull == 0)
        load = rider_load(trial, front, draft, cycle_s, None)
        low = load.w_prime_low_j
        tries.append(
            _Try(
                pull_s=pull,
                intensity=load.np_w / spec.ftp,
                w_left=low / spec.w_prime if low is not None and spec.w_prime else math.inf,
                over_if=over_if(trial, load, limits),
                below_reserve=below_reserve(trial, load, limits),
            )
        )
    return tries


def _can_fill(caps: dict[int, int], front_time: int, limits: Limits) -> bool:
    """Whether some riders, each on a pull from the shortest up to their cap, can fill the front time.

    Taking the riders with the longest caps, as many as the front time has room for at the
    shortest pull, gives the most room; it works if their caps add up to the front time.

    Args:
        caps: Each rider's longest pull, by primary key (below the shortest pull: sits in).
        front_time: Seconds of front time in the rotation.
        limits: The shortest pull.

    Returns:
        True if the front time can be filled.

    """
    longest = sorted((cap for cap in caps.values() if cap >= limits.min_pull_s), reverse=True)
    pullers = min(len(longest), front_time // limits.min_pull_s)
    return pullers >= 1 and front_time <= sum(longest[:pullers])


def _fill(tries: dict[int, list[_Try]], front_time: int, limits: Limits, rule: _Rule) -> dict[int, _Try] | None:
    """Choose pull lengths that fill one rotation's front time, keeping the worst score lowest.

    A pull only gets harder as it grows, so each rider's usable pulls run from sitting in up
    to a longest. For a trial ceiling on the score, a rider's cap is their longest usable pull
    under it; bisecting over the scores that occur finds the lowest ceiling at which the caps
    can fill the front time. The front time is then handed out: the riders with the longest
    caps start on the shortest pull, and each further step goes to whoever it costs least,
    which leaves the scores as even as the steps allow.

    Args:
        tries: Each rider's tries (sitting in, then each pull length), by primary key.
        front_time: Seconds of front time in the rotation.
        limits: The pull grid.
        rule: What to keep lowest, and what every pull must keep to.

    Returns:
        Each rider's chosen try, by primary key, or None if the rotation cannot be filled.

    """
    usable: dict[int, list[_Try]] = {}
    for pk, options in tries.items():
        usable[pk] = list(itertools.takewhile(rule.allowed, options))
        if not usable[pk]:
            return None  # cannot even sit in

    def caps_under(ceiling: float) -> dict[int, int] | None:
        caps = {}
        for pk, options in usable.items():
            fitting = list(itertools.takewhile(lambda t: rule.score(t) <= ceiling, options))
            if not fitting:
                return None
            caps[pk] = fitting[-1].pull_s
        return caps

    ceilings = sorted({rule.score(t) for options in usable.values() for t in options})
    lo, hi, found = 0, len(ceilings) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        caps = caps_under(ceilings[mid])
        if caps is not None and _can_fill(caps, front_time, limits):
            found, hi = caps, mid - 1
        else:
            lo = mid + 1
    if found is None:
        return None

    by_pull = {pk: {t.pull_s: t for t in options} for pk, options in usable.items()}
    able = sorted((pk for pk, cap in found.items() if cap >= limits.min_pull_s), key=lambda pk: -found[pk])
    pullers = able[: front_time // limits.min_pull_s]
    pulls = dict.fromkeys(found, 0)
    for pk in pullers:
        pulls[pk] = limits.min_pull_s
    remaining = front_time - limits.min_pull_s * len(pullers)
    while remaining >= limits.step_s:
        room = [pk for pk in pullers if pulls[pk] + limits.step_s <= found[pk]]
        if not room:
            break
        pk = min(room, key=lambda k: rule.score(by_pull[k][pulls[k] + limits.step_s]))
        pulls[pk] += limits.step_s
        remaining -= limits.step_s
    return {pk: by_pull[pk][pull] for pk, pull in pulls.items()}


def _best_rotation(
    balanced: list[RiderSpec],
    fixed_front: int,
    powers: _Powers,
    speed: float,
    limits: Limits,
    rule: _Rule,
) -> dict[int, _Try] | None:
    """Try every rotation length at one speed and keep the pulls the rule likes best.

    Rotations are compared on their riders' scores, worst first (so the second-worst breaks
    a tie on the worst, and so on), then on ``rule.then``, then the shorter rotation wins.

    Args:
        balanced: The riders Optimize sets (those with an FTP).
        fixed_front: Front time of the riders it leaves alone, in seconds.
        powers: Pull and sitting-in powers.
        speed: The flat speed, in km/h.
        limits: The plan's limits and pull grid.
        rule: What to keep lowest, and what every pull must keep to.

    Returns:
        Each rider's chosen try, by primary key, or None if no rotation works.

    """
    grid = limits.pull_grid()
    level = {s.pk: (powers.front(s, speed), powers.draft(s, speed)) for s in balanced}
    best_key, best = None, None
    for front_time in range(limits.min_pull_s, len(balanced) * limits.max_pull_s + 1, limits.step_s):
        lengths = [d for d in grid if d <= front_time]
        tries = {s.pk: _tries(s, *level[s.pk], front_time + fixed_front, lengths, limits) for s in balanced}
        chosen = _fill(tries, front_time, limits, rule)
        if chosen is None:
            continue
        scores = tuple(sorted((round(rule.score(t), 4) for t in chosen.values()), reverse=True))
        key = (scores, round(max(rule.then(t) for t in chosen.values()), 4), front_time)
        if best_key is None or key < best_key:
            best_key, best = key, chosen
    return best


def _longest_pull(
    spec: RiderSpec,
    front: float,
    draft: float,
    cycle_s: int,
    limits: Limits,
    lengths: list[int],
) -> int:
    """Return the longest of ``lengths`` a rider can pull within both limits, or 0 if none.

    Args:
        spec: The rider.
        front: Their power on the front.
        draft: Their power sitting in.
        cycle_s: The rotation length.
        limits: Target IF and W′ reserve.
        lengths: The pull lengths allowed, shortest first.

    Returns:
        The longest allowed pull in seconds, judged for a long race; 0 if even the shortest breaks a limit.

    """
    lo, hi, best = 0, len(lengths) - 1, 0
    while lo <= hi:  # the limits only tighten as the pull grows, so bisect
        mid = (lo + hi) // 2
        trial = replace(spec, pull_s=lengths[mid], zero_pull=False)
        if within_limits(trial, rider_load(trial, front, draft, cycle_s, None), limits):
            best, lo = lengths[mid], mid + 1
        else:
            hi = mid - 1
    return best


def _fits_somehow(balanced: list[RiderSpec], fixed_front: int, powers: _Powers, speed: float, limits: Limits) -> bool:
    """Whether some pull lengths keep every rider within both limits at this speed.

    Every rider has to be able to sit in; then rotations are tried from the shortest, each
    rider's cap being their longest pull within the limits, until one can be filled.

    Args:
        balanced: The riders Optimize sets (those with an FTP).
        fixed_front: Front time of the riders it leaves alone, in seconds.
        powers: Pull and sitting-in powers.
        speed: The flat speed, in km/h.
        limits: The plan's limits and pull grid.

    Returns:
        True if a rotation works.

    """
    level = {s.pk: (powers.front(s, speed), powers.draft(s, speed)) for s in balanced}
    for spec in balanced:  # everyone has to hold the wheel
        sitting = replace(spec, zero_pull=True)
        if not within_limits(sitting, rider_load(sitting, *level[spec.pk], 0, None), limits):
            return False
    grid = limits.pull_grid()
    for front_time in range(limits.min_pull_s, len(balanced) * limits.max_pull_s + 1, limits.step_s):
        lengths = [d for d in grid if d <= front_time]
        cycle = front_time + fixed_front
        caps = {s.pk: _longest_pull(s, *level[s.pk], cycle, limits, lengths) for s in balanced}
        if _can_fill(caps, front_time, limits):
            return True
    return False


def _assignments(balanced: list[RiderSpec], fixed: list[RiderSpec], chosen: dict[int, _Try]) -> list[BalanceAssignment]:
    """Turn chosen pulls into assignments: longest pull first, then those who sit in, then the rest.

    Args:
        balanced: The riders Optimize set.
        fixed: The riders it left alone (no FTP); their settings stand and they go last.
        chosen: Each balanced rider's chosen try, by primary key.

    Returns:
        One :class:`BalanceAssignment` per rider, numbered in pull order.

    """
    ftp = {s.pk: s.ftp or 0.0 for s in balanced}
    pullers = sorted((pk for pk, t in chosen.items() if t.pull_s), key=lambda pk: (-chosen[pk].pull_s, -ftp[pk]))
    out: list[BalanceAssignment] = [BalanceAssignment(pk, chosen[pk].pull_s, False, i) for i, pk in enumerate(pullers)]
    for spec in balanced:
        if not chosen[spec.pk].pull_s:  # sits in at this speed
            out.append(BalanceAssignment(spec.pk, spec.pull_s, True, len(out)))
    for spec in fixed:
        out.append(BalanceAssignment(spec.pk, spec.pull_s, spec.zero_pull, len(out)))
    return out


def optimize_plan(
    plan: TttPlan, *, target_if: float | None = None, params: physics.PhysicsParams | None = None
) -> OptimizeResult | None:
    """Carry out the plan's goal: its ``speed_goal`` and ``pull_rule``.

    - Keep my pull lengths: as fast as the limits allow finds the fastest speed for the
      pulls as they are (:func:`sustainable_speed`); holding the speed changes nothing.
    - As fast as the limits allow, with Even effort or Most W′ in reserve: a bisection finds
      the fastest speed at which some pulls keep every rider within both limits. At that
      speed the rule picks the pulls, and the speed is then set by :func:`sustainable_speed`'s
      rule for them, so a second Optimize with Keep my pull lengths changes nothing.
    - Holding the speed: the rule picks the pulls at the plan's speed. Even effort keeps the
      highest IF lowest, keeping everyone's W′ above the reserve; Most W′ in reserve keeps
      the lowest W′ highest, keeping everyone's IF at or under Target IF. When the speed is
      too fast for that other limit, it is set aside (``relaxed``).

    Pulls are judged for a long race, the worst case for W′, so they do not depend on the
    route. Riders without an FTP cannot be judged: they keep their settings and go last.

    Args:
        plan: The plan.
        target_if: Intensity factor target; defaults to the plan's ``target_if``.
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        An :class:`OptimizeResult`, or None if no rider has an FTP or no speed works.

    """
    if params is None:
        params = params_for_plan(plan)
    limits = Limits.for_plan(plan, target_if)
    holding = plan.speed_goal == HOLD
    if plan.pull_rule == KEEP:
        if holding:
            return OptimizeResult(speed_kph=float(plan.target_speed_kph), assignments=None)
        return OptimizeResult(speed_kph=sustainable_speed(plan, target_if=target_if, params=params), assignments=None)

    specs = [rider_spec(r) for r in plan.riders.all()]
    balanced = [s for s in specs if s.ftp]
    if not balanced:
        return None
    fixed = [s for s in specs if not s.ftp]
    fixed_front = sum(s.pull_s for s in fixed if s.pulls)
    powers = _Powers(params, _mean_draft_factor(len(specs), params))

    relaxed = False
    if holding:
        speed = float(plan.target_speed_kph)
        rule = _rule(plan.pull_rule, hold_if=plan.pull_rule == RESERVE, hold_w_prime=plan.pull_rule == EVEN)
        chosen = _best_rotation(balanced, fixed_front, powers, speed, limits, rule)
        if chosen is None:
            relaxed = True
            free = _rule(plan.pull_rule, hold_if=False, hold_w_prime=False)
            chosen = _best_rotation(balanced, fixed_front, powers, speed, limits, free)
        if chosen is None:
            return None
    else:
        lo, hi, found = *_SPEED_RANGE_KPH, False
        for _ in range(_BALANCE_STEPS):
            mid = (lo + hi) / 2.0
            if _fits_somehow(balanced, fixed_front, powers, mid, limits):
                lo, found = mid, True
            else:
                hi = mid
        if not found:
            return None
        rule = _rule(plan.pull_rule, hold_if=True, hold_w_prime=True)
        chosen = _best_rotation(balanced, fixed_front, powers, lo, limits, rule)
        if chosen is None:
            return None
        planned = [
            replace(s, pull_s=chosen[s.pk].pull_s or s.pull_s, zero_pull=not chosen[s.pk].pull_s) for s in balanced
        ]
        speed = _max_speed(plan, planned + fixed, limits, params, fallback=math.floor(lo * 10) / 10)

    return OptimizeResult(speed_kph=speed, assignments=_assignments(balanced, fixed, chosen), relaxed=relaxed)


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

    params = physics.params_from_constance(cda_coef_key="STD_CDA_COEF", bike_cda_key=None)
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
