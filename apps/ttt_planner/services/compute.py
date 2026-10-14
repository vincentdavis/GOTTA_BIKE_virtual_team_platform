"""Compute TTT plan results (pull power, load, W′ balance, finish time) from riders.

The target speed is a **flat** speed: every power here (pull, sitting in, and the limits
Calculate speed and Auto-balance work to) is the power to hold it on level road. The
route's climbing enters once, in the finish-time estimate (``physics.estimate_time_seconds``),
which turns that flat-speed effort into a speed on the course.

Through a rotation, a rider's power is a square wave: their pull power on the front, their
sitting-in power for the rest. ``services/effort.py`` turns that into Normalized Power (so
IF and TSS) and the lowest W′ balance. Calculate speed and Auto-balance hold every rider with
an FTP to two limits: IF (NP / FTP) at or under Target IF, and W′bal at or above the plan's
W′ reserve. That includes riders who take no pulls: they still have to hold the wheel, so a
plan never goes faster by leaving a rider to be dropped. ``ttt_planner/help.html`` explains
all of this to riders, so keep it in step with this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

from apps.ttt_planner.services import effort, physics

if TYPE_CHECKING:
    from apps.ttt_planner.models import PlanRider, TttPlan

# Defaults used when a rider has no weight/height on file, so the model still runs.
FALLBACK_WEIGHT_KG = 75.0
FALLBACK_HEIGHT_CM = 178.0
# W′ for a rider without a Zwift Racing value, close to the team's average (about 19.5 kJ).
FALLBACK_W_PRIME_J = 20_000
# Zwift Racing reports a W′ of 0 for some riders; anything this small counts as missing.
MIN_CREDIBLE_W_PRIME_J = 1_000
# Allowed pull steps for Auto-balance, in seconds.
PULL_STEPS_S = (5, 10, 15, 30)
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
        """Return every pull length Auto-balance may hand out.

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
    # Route distance over the estimated time: the target flat speed once the route's
    # climbing is applied (0.0 without a route).
    course_avg_kph: float = 0.0
    # Whether any rider is computed on the fallback weight/height below.
    any_estimated: bool = False
    # Whether any rider's CP or W′ is a fallback rather than a Zwift Racing value.
    any_power_estimated: bool = False
    target_if: float = 0.95
    w_prime_reserve_pct: int = 25
    fallback_weight_kg: ClassVar[float] = FALLBACK_WEIGHT_KG
    fallback_height_cm: ClassVar[float] = FALLBACK_HEIGHT_CM
    fallback_w_prime_kj: ClassVar[float] = FALLBACK_W_PRIME_J / 1000


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


def _race_seconds(plan: TttPlan, specs: list[RiderSpec], speed: float, params: physics.PhysicsParams) -> float | None:
    """Return the estimated race length at a target speed, or None without a route.

    Args:
        plan: The plan.
        specs: Its riders (their average weight and height stand in for the team).
        speed: Target flat speed, in km/h.
        params: Physics constants.

    Returns:
        Race seconds, or None.

    """
    if not plan.route or speed <= 0:
        return None
    count = len(specs) or 1
    seconds = physics.estimate_time_seconds(
        float(plan.route.distance_km),
        plan.route.ascent_m,
        speed,
        avg_weight_kg=sum(s.weight_kg for s in specs) / count if specs else FALLBACK_WEIGHT_KG,
        avg_height_cm=sum(s.height_cm for s in specs) / count if specs else FALLBACK_HEIGHT_CM,
        params=params,
    )
    return seconds if seconds > 0 else None


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
    race_s = _race_seconds(plan, specs, target, params)

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
    return PlanResult(
        riders=results,
        target_speed_kph=target,
        avg_team_power_w=round(sum(r.avg_power_w for r in results) / len(results)) if results else 0,
        estimated_time_s=estimated_time,
        draft_table=_draft_table(params),
        draft_savings_input=format_draft_savings_input(params),
        cda_coef=params.cda_coef,
        course_avg_kph=(round(float(plan.route.distance_km) / estimated_time * 3600, 1) if estimated_time > 0 else 0.0),
        any_estimated=any(s.missing_body for s in specs),
        any_power_estimated=any(s.cp_estimated or s.w_prime_estimated for s in specs),
        target_if=limits.target_if,
        w_prime_reserve_pct=round(limits.reserve * 100),
    )


def _max_speed(
    plan: TttPlan,
    specs: list[RiderSpec],
    limits: Limits,
    params: physics.PhysicsParams,
    *,
    fallback: float,
) -> float:
    """Return the fastest flat speed (to 0.1 km/h, rounded down) at which every rider is within the limits.

    Args:
        plan: The plan (for its route).
        specs: Its riders, with the pull lengths to judge.
        limits: Target IF and W′ reserve.
        params: Physics constants.
        fallback: Returned when nobody pulls, nobody has an FTP, or no speed fits.

    Returns:
        The speed in km/h.

    """
    judged = [s for s in specs if s.ftp]
    if not judged or not any(s.pulls for s in specs):
        return fallback
    powers = _Powers(params, _mean_draft_factor(len(specs), params))
    cycle = sum(s.pull_s for s in specs if s.pulls)

    def fits(speed: float) -> bool:
        race_s = _race_seconds(plan, specs, speed, params)
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
    return math.floor(lo * 10) / 10 if lo >= 0.1 else fallback


def sustainable_speed(
    plan: TttPlan, *, target_if: float | None = None, params: physics.PhysicsParams | None = None
) -> float:
    """Solve for the fastest flat speed at which every rider who pulls stays within the limits.

    Keeps the pull lengths as they are. Every rider with an FTP must keep IF (NP / FTP) at
    or under Target IF and their W′ balance at or above the plan's W′ reserve, riders who
    take no pulls too; the first rider to reach a limit sets the speed.

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


def _longest_pull(
    spec: RiderSpec,
    front: float,
    draft: float,
    cycle_s: int,
    limits: Limits,
    grid: list[int],
) -> int | None:
    """Return the longest pull on the grid a rider can take in a rotation of ``cycle_s``.

    Judged for a long race (the worst case for W′), so a plan that fits here fits any race.

    Args:
        spec: The rider.
        front: Their power on the front.
        draft: Their power sitting in.
        cycle_s: The rotation length.
        limits: Target IF and W′ reserve.
        grid: The pull lengths allowed, shortest first.

    Returns:
        The longest allowed pull, or None if even the shortest breaks a limit.

    """
    candidates = [d for d in grid if d <= cycle_s]
    lo, hi, best = 0, len(candidates) - 1, None
    while lo <= hi:  # the limits only tighten as the pull grows, so bisect
        mid = (lo + hi) // 2
        trial = replace(spec, pull_s=candidates[mid], zero_pull=False)
        if not within_limits(trial, rider_load(trial, front, draft, cycle_s, None), limits):
            hi = mid - 1
        else:
            best, lo = candidates[mid], mid + 1
    return best


def _share_out(front_time: int, caps: dict[int, int], limits: Limits) -> dict[int, int]:
    """Split a rotation's front time between riders, within each one's longest pull.

    Everyone starts on the shortest pull; each further step goes to the rider with the most
    room left under their longest pull, so the spare capacity ends up evenly spread.

    Args:
        front_time: Seconds of front time to hand out.
        caps: Each rider's longest pull, by primary key.
        limits: The plan's limits (shortest pull and step).

    Returns:
        Pull seconds by rider primary key.

    """
    pulls = dict.fromkeys(caps, limits.min_pull_s)
    remaining = front_time - limits.min_pull_s * len(caps)
    while remaining >= limits.step_s:
        room = [pk for pk in caps if pulls[pk] + limits.step_s <= caps[pk]]
        if not room:
            break
        pk = max(room, key=lambda k: (caps[k] - pulls[k], caps[k]))
        pulls[pk] += limits.step_s
        remaining -= limits.step_s
    return pulls


def compute_auto_balance(
    plan: TttPlan, *, target_if: float | None = None, params: physics.PhysicsParams | None = None
) -> BalanceResult | None:
    """Choose pull lengths, order and speed so the team goes as fast as the limits allow.

    For a trial speed, every rider with an FTP must first be able to sit in within the
    limits; if one cannot, the speed is too fast. Then each rotation length is tried from
    the shortest: every rider gets the longest pull on the plan's grid that keeps them
    within the limits in that rotation (riders who cannot manage the shortest pull sit in),
    and the rotation works if those pulls can fill it. A bisection finds the fastest speed with a working rotation,
    whose front time is then shared out by :func:`_share_out`. Pulls are judged for a long
    race; the speed is then set by :func:`sustainable_speed`'s rule for the race actually
    planned, so Auto-balance and Calculate speed always agree. Riders without an FTP keep
    their settings and go last.

    Args:
        plan: The plan.
        target_if: Intensity factor target; defaults to the plan's ``target_if``.
        params: Physics constants; falls back to the plan's resolved params.

    Returns:
        A :class:`BalanceResult`, or None if no rider has an FTP or no speed works.

    """
    if params is None:
        params = params_for_plan(plan)
    limits = Limits.for_plan(plan, target_if)
    specs = [rider_spec(r) for r in plan.riders.all()]
    balanced = [s for s in specs if s.ftp]
    if not balanced:
        return None
    fixed = [s for s in specs if not s.ftp]
    fixed_front = sum(s.pull_s for s in fixed if s.pulls)
    powers = _Powers(params, _mean_draft_factor(len(specs), params))
    grid = limits.pull_grid()

    def rotation_at(speed: float) -> dict[int, int] | None:
        level = {s.pk: (powers.front(s, speed), powers.draft(s, speed)) for s in balanced}
        # Everyone has to hold the wheel: a rider who cannot even sit in caps the speed.
        for spec in balanced:
            sitting = replace(spec, zero_pull=True)
            if not within_limits(sitting, rider_load(sitting, *level[spec.pk], 0, None), limits):
                return None
        for front_time in range(limits.min_pull_s, len(balanced) * limits.max_pull_s + 1, limits.step_s):
            cycle = front_time + fixed_front
            caps = {}
            for spec in balanced:
                cap = _longest_pull(spec, *level[spec.pk], cycle, limits, [d for d in grid if d <= front_time])
                if cap is not None:
                    caps[spec.pk] = cap
            if caps and len(caps) * limits.min_pull_s <= front_time <= sum(caps.values()):
                return _share_out(front_time, caps, limits)
        return None

    lo, hi = _SPEED_RANGE_KPH
    best: dict[int, int] | None = None
    for _ in range(_BALANCE_STEPS):
        mid = (lo + hi) / 2.0
        rotation = rotation_at(mid)
        if rotation is None:
            hi = mid
        else:
            lo, best = mid, rotation
    if best is None:
        return None

    ftp_by_pk = {s.pk: s.ftp for s in balanced}
    pullers = sorted(best, key=lambda pk: (-best[pk], -(ftp_by_pk[pk] or 0)))
    planned = [
        replace(s, pull_s=best[s.pk], zero_pull=False) if s.pk in best else replace(s, zero_pull=True) for s in balanced
    ] + fixed
    speed = _max_speed(plan, planned, limits, params, fallback=math.floor(lo * 10) / 10)

    assignments: list[BalanceAssignment] = []
    for pk in pullers:
        assignments.append(BalanceAssignment(pk, best[pk], False, len(assignments)))
    for spec in balanced:
        if spec.pk not in best:  # sits in at this speed
            assignments.append(BalanceAssignment(spec.pk, spec.pull_s, True, len(assignments)))
    for spec in fixed:  # no FTP to judge them by: their settings stand
        assignments.append(BalanceAssignment(spec.pk, spec.pull_s, spec.zero_pull, len(assignments)))
    return BalanceResult(speed_kph=speed, assignments=assignments)


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
