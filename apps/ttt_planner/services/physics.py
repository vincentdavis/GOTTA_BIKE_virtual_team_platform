"""Cycling power<->speed physics for the TTT planner.

Pure functions with no Django dependency so they are trivially unit-testable.
The model is the standard cycling power equation (rolling resistance + gravity +
aerodynamic drag), tuned for Zwift's physics engine. Zwift has no wind, so the
air speed equals ground speed, and drafting is modelled as a reduction of the
aerodynamic term.

All constants live on :class:`PhysicsParams` with sensible defaults. The web
layer builds a ``PhysicsParams`` from Constance via :func:`params_from_constance`
so the model can be calibrated against Zwift Insider / zwiftgopher without a
redeploy. The defaults are calibrated so a ~75 kg / 180 cm rider needs roughly
290-300 W to hold 40 km/h solo on the flat, matching typical Zwift behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

GRAVITY = 9.8067  # m/s^2

# Aerodynamic draft savings (fraction of aero power saved) by wheel position.
# Index 0 is the rider on the front (the puller) and always saves nothing.
# Seeded from Zwift Insider Pack Dynamics 4 TTT tests (~23/30/37%).
DEFAULT_DRAFT_SAVINGS: tuple[float, ...] = (0.0, 0.233, 0.30, 0.366, 0.39, 0.40, 0.41, 0.42)


@dataclass(frozen=True)
class PhysicsParams:
    """Tunable constants for the power<->speed model.

    Attributes:
        air_density: Air density rho in kg/m^3.
        crr: Coefficient of rolling resistance.
        bike_mass_kg: Mass of the bike + wheels added to rider weight.
        drivetrain_efficiency: Fraction of rider power reaching the wheel (1.0 = no loss).
        cda_coef: Leading coefficient of the CdA estimate (folds in Cd).
        cda_height_exp: Exponent applied to rider height (metres) in the CdA estimate.
        cda_weight_exp: Exponent applied to rider weight (kg) in the CdA estimate.
        draft_savings: Aero-power savings fraction by wheel position (index 0 = front).

    """

    air_density: float = 1.225
    crr: float = 0.004
    bike_mass_kg: float = 8.0
    drivetrain_efficiency: float = 1.0
    cda_coef: float = 0.0318
    cda_height_exp: float = 0.725
    cda_weight_exp: float = 0.425
    draft_savings: tuple[float, ...] = field(default_factory=lambda: DEFAULT_DRAFT_SAVINGS)


DEFAULT_PARAMS = PhysicsParams()


def estimate_cda(height_cm: float, weight_kg: float, params: PhysicsParams = DEFAULT_PARAMS) -> float:
    """Estimate a rider's CdA (drag area, m^2) from height and weight.

    Uses a DuBois-style frontal-area approximation scaled by ``cda_coef`` (which
    folds in the drag coefficient).

    Args:
        height_cm: Rider height in centimetres.
        weight_kg: Rider weight in kilograms.
        params: Physics constants.

    Returns:
        Estimated CdA in square metres.

    """
    height_m = height_cm / 100.0
    return params.cda_coef * (height_m**params.cda_height_exp) * (weight_kg**params.cda_weight_exp)


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


def params_from_constance(cda_coef_key: str = "TTT_CDA_COEF") -> PhysicsParams:
    """Build :class:`PhysicsParams` from Constance settings.

    Imports Constance lazily so this module stays import-safe without Django.

    Args:
        cda_coef_key: Which Constance key supplies the CdA leading coefficient.
            Defaults to ``TTT_CDA_COEF`` (aero tuck); pass ``STD_CDA_COEF`` for
            general road/climbing racing (upright position).

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
        draft_savings=savings,
    )
