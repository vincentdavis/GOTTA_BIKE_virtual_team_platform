"""Climb comparison engine, shared by the ladder and TTT planners.

Combines a rider's power-duration curve with the cycling power model in
``physics.py`` to estimate how fast each rider climbs a given slope, and which
team is favored across a grid of climb lengths and grades.

The core idea: for a climb that takes ``t`` seconds a rider can hold ``P(t)``
(interpolated from their power curve); that power yields a climb speed via the
physics model, which in turn implies the time for a climb of a given length.
Solving that fixed point gives a per-rider climb time, and ranking riders across
both teams gives a team advantage. Because riders' power-duration curves decline
at different rates, the favored team changes with climb length — which is exactly
what the heatmap visualizes.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from itertools import pairwise

from apps.ttt_planner.services import physics

# Power-duration sample points (seconds) matching the stored rider curves
# (ladder ``zr_data`` and TTT power fields share the same buckets).
CURVE_SECONDS: tuple[int, ...] = (5, 15, 30, 60, 120, 300, 1200)

# Fallback height when a rider has none recorded (CdA matters little on steep
# climbs, so a nominal value is fine).
DEFAULT_HEIGHT_CM = 175.0

# Climb scoring: 1st = TOP_POINTS, one fewer per place, floored at 0 (mirrors the
# ladder projected-score scheme so the heatmap reads consistently).
TOP_POINTS = 10


@dataclass(frozen=True)
class ClimbRider:
    """A rider reduced to what the climb model needs.

    Attributes:
        name: Display name.
        weight_kg: Rider weight in kilograms.
        height_cm: Rider height in centimetres (0/None falls back to a default).
        power_curve: Map of duration-seconds -> watts (a subset of CURVE_SECONDS
            is fine; missing/zero points are ignored).
        side: Optional team tag ("ours"/"opp" etc.) for grouping.

    """

    name: str
    weight_kg: float
    height_cm: float
    power_curve: dict[int, float] = field(default_factory=dict)
    side: str = ""


@dataclass(frozen=True)
class ClimbResult:
    """A rider's steady best effort on one climb.

    Attributes:
        time_s: Time to clear the climb in seconds.
        speed_kph: Average climb speed in km/h.
        power_w: Sustained power in watts.
        wkg: Sustained power per kilogram.

    """

    time_s: float
    speed_kph: float
    power_w: float
    wkg: float


def sustainable_power(power_curve: dict[int, float], seconds: float) -> float | None:
    """Interpolate holdable power (W) at an arbitrary duration.

    Uses log-time interpolation between the curve points and clamps outside the
    sampled range (so durations beyond 20 min reuse the 20-min value).

    Args:
        power_curve: Map of duration-seconds -> watts.
        seconds: Effort duration to evaluate.

    Returns:
        Interpolated power in watts, or None if the curve has no usable points.

    """
    pts = sorted((s, w) for s, w in power_curve.items() if w and w > 0)
    if not pts:
        return None
    if seconds <= pts[0][0]:
        return pts[0][1]
    if seconds >= pts[-1][0]:
        return pts[-1][1]
    for (s0, w0), (s1, w1) in pairwise(pts):
        if seconds <= s1:
            frac = (math.log(seconds) - math.log(s0)) / (math.log(s1) - math.log(s0))
            return w0 + (w1 - w0) * frac
    return pts[-1][1]


def climb_effort(
    rider: ClimbRider,
    length_m: float,
    grade: float,
    *,
    draft_factor: float = 1.0,
    params: physics.PhysicsParams = physics.DEFAULT_PARAMS,
) -> ClimbResult | None:
    """Solve a rider's steady best effort up a climb.

    Fixed-point iteration: a guessed climb time picks a sustainable power, which
    yields a speed, which implies a new climb time; repeat until stable.

    Args:
        rider: The rider.
        length_m: Climb length in metres.
        grade: Gradient as a fraction (0.08 = 8%).
        draft_factor: Aero multiplier (1.0 = no draft, 0.7 = 30% aero saving).
        params: Physics constants.

    Returns:
        The climb result, or None if the rider lacks weight/power data.

    """
    if rider.weight_kg <= 0 or length_m <= 0:
        return None
    height = rider.height_cm or DEFAULT_HEIGHT_CM

    seconds = 60.0  # initial guess
    power = sustainable_power(rider.power_curve, seconds)
    if power is None:
        return None
    for _ in range(12):
        power = sustainable_power(rider.power_curve, seconds)
        speed_kph = physics.speed_for_power(
            power, weight_kg=rider.weight_kg, height_cm=height, grade=grade, draft_factor=draft_factor, params=params
        )
        speed_ms = speed_kph / 3.6
        if speed_ms <= 0:
            return None
        new_seconds = length_m / speed_ms
        if abs(new_seconds - seconds) < 0.5:
            seconds = new_seconds
            break
        seconds = new_seconds

    power = sustainable_power(rider.power_curve, seconds)
    speed_kph = physics.speed_for_power(
        power, weight_kg=rider.weight_kg, height_cm=height, grade=grade, draft_factor=draft_factor, params=params
    )
    return ClimbResult(time_s=seconds, speed_kph=speed_kph, power_w=power, wkg=power / rider.weight_kg)


def _points(finish: int) -> int:
    """Ladder-style points for a finishing place.

    Args:
        finish: 1-based finishing position.

    Returns:
        Points for that place (1st = TOP_POINTS, floored at 0).

    """
    return max(0, TOP_POINTS + 1 - finish)


def climb_matchup(
    our: list[ClimbRider],
    opp: list[ClimbRider],
    length_m: float,
    grade: float,
    *,
    draft_factor: float = 1.0,
    params: physics.PhysicsParams = physics.DEFAULT_PARAMS,
) -> dict:
    """Rank both teams' riders on one climb and score the result.

    Args:
        our: Our riders.
        opp: Opponent riders.
        length_m: Climb length in metres.
        grade: Gradient as a fraction.
        draft_factor: Aero multiplier.
        params: Physics constants.

    Returns:
        Dict with ``our_points``, ``opp_points``, ``margin`` (our - opp), the
        fastest time per side (``our_best_s`` / ``opp_best_s``), and the median
        time per side (``our_median_s`` / ``opp_median_s``). Times may be None.

    """
    ranked = []
    for side, riders in (("ours", our), ("opp", opp)):
        for rider in riders:
            res = climb_effort(rider, length_m, grade, draft_factor=draft_factor, params=params)
            if res is not None:
                ranked.append((side, res.time_s))
    ranked.sort(key=lambda r: r[1])

    our_points = opp_points = 0
    for finish, (side, _t) in enumerate(ranked, start=1):
        if side == "ours":
            our_points += _points(finish)
        else:
            opp_points += _points(finish)

    our_times = [t for s, t in ranked if s == "ours"]
    opp_times = [t for s, t in ranked if s == "opp"]
    return {
        "our_points": our_points,
        "opp_points": opp_points,
        "margin": our_points - opp_points,
        "our_best_s": min(our_times) if our_times else None,
        "opp_best_s": min(opp_times) if opp_times else None,
        "our_median_s": statistics.median(our_times) if our_times else None,
        "opp_median_s": statistics.median(opp_times) if opp_times else None,
    }


def advantage_grid(
    our: list[ClimbRider],
    opp: list[ClimbRider],
    lengths_m: list[float],
    grades: list[float],
    *,
    draft_factor: float = 1.0,
    params: physics.PhysicsParams = physics.DEFAULT_PARAMS,
) -> list[dict]:
    """Compute the favored team across a grid of climb lengths and grades.

    Args:
        our: Our riders.
        opp: Opponent riders.
        lengths_m: Climb lengths (metres) — the heatmap columns.
        grades: Gradients as fractions — the heatmap rows.
        draft_factor: Aero multiplier.
        params: Physics constants.

    Returns:
        One row per grade, each ``{"grade", "cells": [matchup-dict per length]}``.

    """
    rows = []
    for grade in grades:
        cells = [
            climb_matchup(our, opp, length, grade, draft_factor=draft_factor, params=params) | {"length_m": length}
            for length in lengths_m
        ]
        rows.append({"grade": grade, "cells": cells})
    return rows
