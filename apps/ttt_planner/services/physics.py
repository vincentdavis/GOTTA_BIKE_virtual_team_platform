"""Cycling power<->speed physics for the TTT planner.

Pure functions with no Django dependency so they are trivially unit-testable.
The model is the standard cycling power equation (rolling resistance + gravity +
aerodynamic drag), tuned for Zwift's physics engine. Zwift has no wind, so the
air speed equals ground speed, and drafting is modelled as a reduction of the
aerodynamic term.

All constants live on :class:`PhysicsParams` with sensible defaults. The web
layer builds a ``PhysicsParams`` from Constance via :func:`params_from_constance`
so the model can be calibrated without a redeploy.

A rider's drag area is their body's, from height and weight, plus their bike's. The
body formula's defaults match Zwift's own (half of 0.3176 x the Shuter-Aslani body
surface area, recovered from the game by the Zwift Speed Lab) to within about 1% for
ordinary builds. Zwift has no riding position: what makes a TT faster is the
equipment, a fixed drag area per item, which ``bike_cda_m2`` carries. The TTT planner
assumes Zwift's TT bike (``TTT_BIKE_CDA_M2``); :data:`DEFAULT_PARAMS` leave the bike
out, which is close to a road bike.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

GRAVITY = 9.8067  # m/s^2

# Aerodynamic draft savings (fraction of aero power saved) by wheel position.
# Index 0 is the rider on the front (the puller) and always saves nothing.
# Seeded from Zwift Insider Pack Dynamics 4 TTT tests (~23/30/37%).
DEFAULT_DRAFT_SAVINGS: tuple[float, ...] = (0.0, 0.233, 0.30, 0.366, 0.39, 0.40, 0.41, 0.42)

# Zwift's TT bike, as the Speed Lab's equipment data has it: a TT frame (-0.030 m² for
# the common ones, down to -0.036) with a deep front wheel (-0.010) and a disc (-0.010).
TT_BIKE_CDA_M2 = -0.050
# The smallest drag area a rider can end up with, whatever the settings say.
MIN_CDA_M2 = 0.05


@dataclass(frozen=True)
class PhysicsParams:
    """Tunable constants for the power<->speed model.

    Attributes:
        air_density: Air density rho in kg/m^3.
        crr: Coefficient of rolling resistance.
        bike_mass_kg: Mass of the bike + wheels added to rider weight.
        drivetrain_efficiency: Fraction of rider power reaching the wheel (1.0 = no loss).
        cda_coef: Leading coefficient of the body's CdA estimate (folds in Cd).
        cda_height_exp: Exponent applied to rider height (metres) in the CdA estimate.
        cda_weight_exp: Exponent applied to rider weight (kg) in the CdA estimate.
        bike_cda_m2: The bike's drag area, added to the body's (Zwift's equipment CdA
            bias; negative = more aero).
        draft_savings: Aero-power savings fraction by wheel position (index 0 = front).

    """

    air_density: float = 1.225
    crr: float = 0.004
    bike_mass_kg: float = 8.0
    drivetrain_efficiency: float = 1.0
    cda_coef: float = 0.0318
    cda_height_exp: float = 0.725
    cda_weight_exp: float = 0.425
    bike_cda_m2: float = 0.0
    draft_savings: tuple[float, ...] = field(default_factory=lambda: DEFAULT_DRAFT_SAVINGS)


DEFAULT_PARAMS = PhysicsParams()


def estimate_cda(height_cm: float, weight_kg: float, params: PhysicsParams = DEFAULT_PARAMS) -> float:
    """Estimate a rider's CdA (drag area, m^2): their body's, from height and weight, plus their bike's.

    The body's is a frontal-area approximation scaled by ``cda_coef`` (which folds in the
    drag coefficient); the bike's is ``bike_cda_m2``, as Zwift adds each item's.

    Args:
        height_cm: Rider height in centimetres.
        weight_kg: Rider weight in kilograms.
        params: Physics constants.

    Returns:
        Estimated CdA in square metres, never below :data:`MIN_CDA_M2`.

    """
    height_m = height_cm / 100.0
    body = params.cda_coef * (height_m**params.cda_height_exp) * (weight_kg**params.cda_weight_exp)
    return max(MIN_CDA_M2, body + params.bike_cda_m2)


def draft_savings_fraction(position: int, params: PhysicsParams = DEFAULT_PARAMS) -> float:
    """Return the aero-power savings fraction for a 1-based wheel position.

    Position 1 (the front) saves nothing. Positions deeper than the configured
    table are clamped to the last value.

    Args:
        position: 1-based wheel position (1 = front).
        params: Physics constants.

    Returns:
        Savings fraction in [0, 1).

    """
    if position <= 1:
        return 0.0
    idx = min(position - 1, len(params.draft_savings) - 1)
    return params.draft_savings[idx]


def power_for_speed(
    speed_kph: float,
    *,
    weight_kg: float,
    height_cm: float,
    grade: float = 0.0,
    draft_factor: float = 1.0,
    params: PhysicsParams = DEFAULT_PARAMS,
) -> float:
    """Compute the rider power (watts) needed to hold a speed.

    Args:
        speed_kph: Target ground speed in km/h.
        weight_kg: Rider weight in kilograms.
        height_cm: Rider height in centimetres.
        grade: Road gradient as a fraction (0.05 = 5% climb, negative = descent).
        draft_factor: Multiplier on the aerodynamic term (1.0 = no draft, 0.7 = 30% saving).
        params: Physics constants.

    Returns:
        Required rider power in watts (clamped at >= 0).

    """
    v = speed_kph / 3.6
    total_mass = weight_kg + params.bike_mass_kg
    cda = estimate_cda(height_cm, weight_kg, params)

    p_rolling = params.crr * total_mass * GRAVITY * v
    p_gravity = total_mass * GRAVITY * grade * v
    p_aero = 0.5 * params.air_density * cda * v**3 * draft_factor

    p_wheel = p_rolling + p_gravity + p_aero
    p_rider = p_wheel / params.drivetrain_efficiency
    return max(0.0, p_rider)


def speed_for_power(
    power_w: float,
    *,
    weight_kg: float,
    height_cm: float,
    grade: float = 0.0,
    draft_factor: float = 1.0,
    params: PhysicsParams = DEFAULT_PARAMS,
) -> float:
    """Invert the power model to find speed (km/h) for a given power.

    Solved numerically by bisection (the power curve is monotonic in speed for
    realistic, non-steep-descent gradients).

    Args:
        power_w: Rider power in watts.
        weight_kg: Rider weight in kilograms.
        height_cm: Rider height in centimetres.
        grade: Road gradient as a fraction.
        draft_factor: Multiplier on the aerodynamic term.
        params: Physics constants.

    Returns:
        Speed in km/h.

    """
    if power_w <= 0:
        return 0.0

    lo, hi = 0.0, 120.0  # km/h search bracket

    def p(speed_kph: float) -> float:
        return power_for_speed(
            speed_kph,
            weight_kg=weight_kg,
            height_cm=height_cm,
            grade=grade,
            draft_factor=draft_factor,
            params=params,
        )

    # Expand the upper bound on the rare chance a rider exceeds 120 km/h (descents).
    while p(hi) < power_w and hi < 200.0:
        hi += 40.0

    for _ in range(60):
        mid = (lo + hi) / 2.0
        if p(mid) < power_w:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def speed_on_grade(
    power_w: float,
    *,
    weight_kg: float,
    height_cm: float,
    grade: float = 0.0,
    draft_factor: float = 1.0,
    params: PhysicsParams = DEFAULT_PARAMS,
) -> float:
    """Return the speed (km/h) a power holds on a gradient, the same answer as :func:`speed_for_power`.

    The power model is a cubic in speed, ``a v³ + b v = P``, so this solves it directly
    instead of bisecting, which makes it cheap enough to time a whole course inside a
    search over speeds. On a descent steeper than rolling resistance (``b < 0``) the cubic
    can have three real roots; the rider's speed is the largest. A Newton step tidies the
    rounding.

    Args:
        power_w: Rider power in watts.
        weight_kg: Rider weight in kilograms.
        height_cm: Rider height in centimetres.
        grade: Road gradient as a fraction.
        draft_factor: Multiplier on the aerodynamic term.
        params: Physics constants.

    Returns:
        Speed in km/h (0.0 for no power on a road that does not pull the rider along).

    """
    total_mass = weight_kg + params.bike_mass_kg
    a = 0.5 * params.air_density * estimate_cda(height_cm, weight_kg, params) * draft_factor
    b = total_mass * GRAVITY * (params.crr + grade)
    wheel = max(0.0, power_w) * params.drivetrain_efficiency
    if a <= 0:
        return wheel / b * 3.6 if b > 0 else 0.0
    if wheel == 0:
        # Coasting: the speed at which gravity is balanced by drag and rolling, if any.
        return math.sqrt(-b / a) * 3.6 if b < 0 else 0.0
    p, q = b / a, -wheel / a  # v³ + p v + q = 0
    disc = (q / 2) ** 2 + (p / 3) ** 3
    if disc >= 0:
        root = math.sqrt(disc)
        v = math.cbrt(-q / 2 + root) + math.cbrt(-q / 2 - root)
    else:  # three real roots (only when p < 0): take the largest
        r = math.sqrt(-p / 3)
        v = 2 * r * math.cos(math.acos(max(-1.0, min(1.0, (-q / 2) / r**3))) / 3)
    slope = 3 * a * v * v + b
    if slope > 0:
        v -= (a * v**3 + b * v - wheel) / slope
    return max(0.0, v) * 3.6


def intensity_factor(power_w: float, ftp_w: float | None) -> float | None:
    """Compute the Intensity Factor (power / FTP).

    Args:
        power_w: Effort power in watts.
        ftp_w: Rider FTP in watts.

    Returns:
        IF as a float, or None if FTP is missing or zero.

    """
    if not ftp_w:
        return None
    return power_w / ftp_w


def normalized_power(power_series: list[float], window: int = 30) -> float:
    """Compute Normalized Power (NP) from a 1-second power series.

    Standard NP algorithm: a trailing rolling average over ``window`` seconds,
    each value raised to the 4th power, averaged, then the 4th root.

    Args:
        power_series: Power in watts at 1-second resolution.
        window: Rolling-average window in seconds (NP convention is 30).

    Returns:
        Normalized Power in watts (0.0 for an empty series).

    """
    n = len(power_series)
    if n == 0:
        return 0.0

    prefix = [0.0] * (n + 1)
    for i, p in enumerate(power_series):
        prefix[i + 1] = prefix[i] + p

    total_fourth = 0.0
    for t in range(1, n + 1):
        start = max(0, t - window)
        rolling_avg = (prefix[t] - prefix[start]) / (t - start)
        total_fourth += rolling_avg**4

    return (total_fourth / n) ** 0.25


def training_stress_score(normalized_power_w: float, ftp_w: float | None, duration_s: float) -> float | None:
    """Compute Training Stress Score (TSS).

    ``TSS = (duration_s * NP * IF) / (FTP * 3600) * 100`` where ``IF = NP / FTP``.

    Args:
        normalized_power_w: Normalized Power in watts.
        ftp_w: Rider FTP in watts.
        duration_s: Effort duration in seconds.

    Returns:
        TSS as a float, or None if FTP is missing/zero.

    """
    if not ftp_w or duration_s <= 0:
        return None
    intensity = normalized_power_w / ftp_w
    return (duration_s / 3600.0) * intensity * intensity * 100.0


def average_grade(distance_km: float, elevation_m: float) -> float:
    """Return the average gradient (fraction) of a route.

    Args:
        distance_km: Route distance in kilometres.
        elevation_m: Total elevation gain in metres.

    Returns:
        Average gradient as a fraction (0.0 if distance is zero).

    """
    if not distance_km:
        return 0.0
    return elevation_m / (distance_km * 1000.0)


def estimate_time_seconds(
    distance_km: float,
    elevation_m: float,
    target_speed_kph: float,
    *,
    avg_weight_kg: float = 75.0,
    avg_height_cm: float = 178.0,
    params: PhysicsParams = DEFAULT_PARAMS,
) -> float:
    """Estimate finish time for a route given a flat target speed.

    The flat target speed implies a team power (via an average rider); that power
    is then re-solved on the route's average gradient to get an adjusted speed.
    This is an MVP approximation -- it uses average gradient, not a
    segment-by-segment elevation profile.

    Args:
        distance_km: Route distance in kilometres.
        elevation_m: Total elevation gain in metres.
        target_speed_kph: Target flat speed in km/h.
        avg_weight_kg: Average rider weight used for the time estimate.
        avg_height_cm: Average rider height used for the time estimate.
        params: Physics constants.

    Returns:
        Estimated time in seconds.

    """
    if not distance_km or target_speed_kph <= 0:
        return 0.0

    grade = average_grade(distance_km, elevation_m)
    if abs(grade) < 1e-6:
        adjusted_speed = target_speed_kph
    else:
        team_power = power_for_speed(target_speed_kph, weight_kg=avg_weight_kg, height_cm=avg_height_cm, params=params)
        adjusted_speed = speed_for_power(
            team_power, weight_kg=avg_weight_kg, height_cm=avg_height_cm, grade=grade, params=params
        )

    if adjusted_speed <= 0:
        return 0.0
    return distance_km / adjusted_speed * 3600.0


def normalize_draft_savings(values: list[float] | tuple[float, ...] | None) -> tuple[float, ...] | None:
    """Normalise a sequence of draft-savings values.

    Values greater than 1 are treated as percentages and divided by 100, so both
    ``0.233`` and ``23.3`` work. Each value is clamped to ``[0, 0.95]``. If the
    first value is non-zero a leading ``0.0`` (the front rider, who never drafts)
    is prepended.

    Args:
        values: Raw numeric values by position.

    Returns:
        A normalised tuple of savings fractions (index 0 = front), or None if the
        input is empty or unusable.

    """
    if not values:
        return None
    try:
        nums = [float(v) for v in values]
    except TypeError, ValueError:
        return None
    if not nums:
        return None

    if max(nums) > 1.0:  # entered as percentages
        nums = [v / 100.0 for v in nums]
    nums = [min(max(v, 0.0), 0.95) for v in nums]
    if nums[0] > 1e-9:
        nums = [0.0, *nums]
    return tuple(nums)


def parse_draft_savings(raw: str | list | tuple | None) -> tuple[float, ...] | None:
    """Parse a draft-savings list from user / Constance / stored-JSON input.

    Accepts an already-parsed list/tuple, a JSON array string
    (``[0.0, 0.233, ...]``), or a plain comma/semicolon separated string
    (``0, 23.3, 30``). The result is run through :func:`normalize_draft_savings`.

    Args:
        raw: The value to parse (sequence or string).

    Returns:
        A normalised tuple of savings fractions (index 0 = front), or None if the
        input is empty or unparseable.

    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return normalize_draft_savings(list(raw))

    if not raw.strip():
        return None

    values: list[float] | None = None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            values = [float(x) for x in loaded]
    except ValueError, TypeError:
        values = None

    if values is None:
        try:
            values = [float(x) for x in raw.replace(";", ",").split(",") if x.strip()]
        except ValueError:
            return None

    return normalize_draft_savings(values)


def params_from_constance(
    cda_coef_key: str = "TTT_CDA_COEF", bike_cda_key: str | None = "TTT_BIKE_CDA_M2"
) -> PhysicsParams:
    """Build :class:`PhysicsParams` from Constance settings.

    Imports Constance lazily so this module stays import-safe without Django.

    Args:
        cda_coef_key: Which Constance key supplies the body's CdA leading coefficient.
            Defaults to ``TTT_CDA_COEF``; pass ``STD_CDA_COEF`` for general road/climbing
            racing (upright position).
        bike_cda_key: Which Constance key supplies the bike's drag area. Defaults to
            ``TTT_BIKE_CDA_M2``, Zwift's TT bike; None leaves the bike out.

    Returns:
        A ``PhysicsParams`` populated from the ``TTT_*`` Constance settings,
        falling back to defaults if a value is missing or malformed.

    """
    from constance import config

    def _f(key: str, default: float) -> float:
        try:
            return float(getattr(config, key))
        except AttributeError, TypeError, ValueError:
            return default

    try:
        savings = parse_draft_savings(config.TTT_DRAFT_SAVINGS) or DEFAULT_DRAFT_SAVINGS
    except AttributeError, TypeError, ValueError:
        savings = DEFAULT_DRAFT_SAVINGS

    return PhysicsParams(
        air_density=_f("TTT_AIR_DENSITY", DEFAULT_PARAMS.air_density),
        crr=_f("TTT_CRR", DEFAULT_PARAMS.crr),
        bike_mass_kg=_f("TTT_BIKE_MASS_KG", DEFAULT_PARAMS.bike_mass_kg),
        drivetrain_efficiency=_f("TTT_DRIVETRAIN_EFFICIENCY", DEFAULT_PARAMS.drivetrain_efficiency),
        cda_coef=_f(cda_coef_key, DEFAULT_PARAMS.cda_coef),
        cda_height_exp=_f("TTT_CDA_HEIGHT_EXP", DEFAULT_PARAMS.cda_height_exp),
        cda_weight_exp=_f("TTT_CDA_WEIGHT_EXP", DEFAULT_PARAMS.cda_weight_exp),
        bike_cda_m2=_f(bike_cda_key, TT_BIKE_CDA_M2) if bike_cda_key else 0.0,
        draft_savings=savings,
    )
