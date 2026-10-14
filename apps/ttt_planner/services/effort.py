"""How hard a rotation is on one rider: Normalized Power and W′ balance.

Pure functions, no Django, so they can be tested on their own. Through a rotation a
rider's power is a run of steady stretches that repeats: their pull on the front, then
the wheels they sit on as the line comes past, each at that wheel's power for as long as
the rider on the front pulls. Both measures are worked out exactly from those stretches,
in time that grows only with their number, which keeps them cheap enough for Optimize to
evaluate thousands of times per click.

W′ balance follows the differential model (Skiba 2015, after Froncioni and Clarke).
Above critical power (CP), W′ drains at ``power - CP`` joules a second. Below it, W′
refills towards full at a rate set by how far below CP the rider is and how much is
missing: ``dW′bal/dt = (W′ - W′bal) * (CP - power) / W′``. Working with the deficit
``u = W′ - W′bal``, each stretch at constant power is an affine map on ``u``: draining
adds ``(power - CP) * seconds``, refilling multiplies by ``exp(-(CP - power) * seconds / W′)``.
"""

from __future__ import annotations

import bisect
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Normalized Power's rolling-average window, in seconds.
NP_WINDOW_S = 30


def _ramp(a: float, b: float) -> float:
    """Return the mean of x⁴ along a straight line from ``a`` to ``b``.

    The usual ``(b⁵ - a⁵) / (5 (b - a))``, multiplied out so that it holds when ``a == b``.

    Returns:
        The mean fourth power.

    """
    return (a**4 + a**3 * b + a**2 * b**2 + a * b**3 + b**4) / 5


def _joined(stretches: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop empty stretches and join neighbours at the same power, round the end of the rotation too.

    Returns:
        ``(watts, seconds)`` stretches, each longer than zero, no two neighbours at one power.

    """
    parts: list[tuple[float, float]] = []
    for watts, seconds in stretches:
        if seconds <= 0:
            continue
        if parts and parts[-1][0] == watts:
            parts[-1] = (watts, parts[-1][1] + seconds)
        else:
            parts.append((watts, seconds))
    if len(parts) > 1 and parts[0][0] == parts[-1][0]:
        watts, seconds = parts.pop()
        parts[0] = (watts, parts[0][1] + seconds)
    return parts


def _rolling_fourth(parts: list[tuple[float, float]], cycle_s: float, window: float) -> float:
    """Return the integral of the fourth power of the rolling average over one rotation.

    The rolling average of a run of steady stretches is a straight line between the moments
    a stretch starts, at either end of the window, so integrating line by line is exact,
    however short the stretches are, and wraps round for a window longer than the rotation.

    Args:
        parts: ``(watts, seconds)`` stretches, each longer than zero.
        cycle_s: Their total length.
        window: The rolling-average window, in seconds.

    Returns:
        The integral, in watts⁴·seconds.

    """
    starts, area, clock = [], [0.0], 0.0
    for watts, seconds in parts:
        starts.append(clock)
        clock += seconds
        area.append(area[-1] + watts * seconds)
    total = area[-1]

    def integral(t: float) -> float:  # ∫ power from 0 to t, for any t (the wave repeats)
        laps, into = divmod(t, cycle_s)
        i = bisect.bisect_right(starts, into) - 1
        return laps * total + area[i] + parts[i][0] * (into - starts[i])

    def rolling(t: float) -> float:
        return (integral(t) - integral(t - window)) / window

    marks = sorted({*starts, *((start + window) % cycle_s for start in starts), cycle_s})
    fourth, last_t, last_r = 0.0, marks[0], rolling(marks[0])
    for t in marks[1:]:
        r = rolling(t)
        fourth += (t - last_t) * _ramp(last_r, r)
        last_t, last_r = t, r
    return fourth


def rotation_np(stretches: Sequence[tuple[float, float]]) -> float:
    """Return the Normalized Power of a repeating rotation of steady stretches.

    This is the steady state of a long race, so it does not depend on the race length.
    NP's 30-second rolling average ramps in a straight line from one level to the next for
    30 seconds after each change; when every stretch lasts that long, the mean of its
    fourth power is a short sum. Shorter stretches never let the average settle, and are
    integrated piece by piece instead (:func:`_rolling_fourth`), which is just as exact.

    Args:
        stretches: ``(watts, seconds)`` in the order ridden; empty ones are ignored.

    Returns:
        Normalized Power in watts (0 for a rotation with no time in it).

    """
    parts = _joined(stretches)
    if not parts:
        return 0.0
    if len(parts) == 1:
        return parts[0][0]
    window = NP_WINDOW_S
    cycle_s = sum(seconds for _, seconds in parts)
    if all(seconds >= window for _, seconds in parts):
        fourth = sum((seconds - window) * watts**4 for watts, seconds in parts)
        fourth += window * sum(_ramp(parts[i - 1][0], watts) for i, (watts, _) in enumerate(parts))
    else:
        fourth = _rolling_fourth(parts, cycle_s, window)
    return (fourth / cycle_s) ** 0.25


def cycle_np(front: float, draft: float, pull_s: float, cycle_s: float) -> float:
    """Return the Normalized Power of a repeating pull: ``front`` for ``pull_s``, ``draft`` for the rest.

    Args:
        front: Power on the front, in watts.
        draft: Power while sitting in, in watts.
        pull_s: Length of the rider's pull, in seconds (0 for a rider who takes no pulls).
        cycle_s: Length of the whole rotation, in seconds.

    Returns:
        Normalized Power in watts.

    """
    if pull_s <= 0 or cycle_s <= 0:
        return draft
    return rotation_np([(front, pull_s), (draft, cycle_s - pull_s)])


def rotation_lowest_w_prime_bal(
    stretches: Sequence[tuple[float, float]],
    cp: float,
    w_prime: float,
    rotations: int | None = None,
) -> float:
    """Return the lowest W′ balance a rider reaches over a race of repeating rotations.

    The race starts with W′ full at the start of the first stretch. Each stretch above CP
    drains it and each one below refills some; from one rotation to the next the balance
    only sinks, until the drain and the refill balance out, so the low point is the lowest
    stretch end of the last rotation.

    Args:
        stretches: ``(watts, seconds)`` in the order ridden; empty ones are ignored.
        cp: Critical power, in watts.
        w_prime: W′, in joules.
        rotations: How many times the rider goes round in the race. ``None`` means a long
            race, where the pattern has settled: the lowest the balance can go.

    Returns:
        The lowest W′bal in joules: ``w_prime`` minus the largest deficit. Below zero when
        the rider runs out; ``-inf`` when a long race drains them without end.

    """
    # Each stretch changes the deficit u = W′ - W′bal affinely (see the module docstring),
    # so a whole rotation does too: u -> scale * u + shift.
    maps = []
    for watts, seconds in stretches:
        if seconds <= 0:
            continue
        if watts > cp:
            maps.append((1.0, (watts - cp) * seconds))
        else:
            maps.append((math.exp(-(cp - watts) * seconds / w_prime), 0.0))
    if not maps:
        return w_prime
    scale, shift = 1.0, 0.0
    for a, b in maps:
        scale, shift = a * scale, a * shift + b
    refills = scale < 1.0 - 1e-12

    if rotations is not None:
        # Starting from full, the deficit only grows from one rotation to the next, so the
        # worst moment is in the last one. Its starting deficit has a closed form, which keeps
        # the cost flat however many rotations the race holds (a near-standstill speed in a
        # speed search makes that billions).
        done = max(rotations, 1) - 1
        start = shift * (1.0 - scale**done) / (1.0 - scale) if refills else shift * done
    elif not refills:
        # Nothing ever refills: any drain at all runs W′ down without end.
        return -math.inf if shift > 1e-9 else w_prime
    else:
        start = shift / (1.0 - scale)  # a long race: the fixed point of one rotation

    worst = deficit = start
    for a, b in maps:
        deficit = a * deficit + b
        worst = max(worst, deficit)
    return w_prime - worst


def lowest_w_prime_bal(
    front: float,
    draft: float,
    pull_s: float,
    cycle_s: float,
    cp: float,
    w_prime: float,
    pulls: int | None = None,
) -> float:
    """Return the lowest W′ balance over a race of repeating pulls: ``front`` for ``pull_s``, ``draft`` for the rest.

    Args:
        front: Power on the front, in watts.
        draft: Power while sitting in, in watts.
        pull_s: Length of the rider's pull, in seconds.
        cycle_s: Length of the whole rotation, in seconds.
        cp: Critical power, in watts.
        w_prime: W′, in joules.
        pulls: How many pulls the rider takes in the race; ``None`` for a long race.

    Returns:
        The lowest W′bal in joules, as :func:`rotation_lowest_w_prime_bal`.

    """
    return rotation_lowest_w_prime_bal([(front, pull_s), (draft, max(cycle_s - pull_s, 0.0))], cp, w_prime, pulls)


def sitting_w_prime_bal(draft: float, cp: float, w_prime: float, race_s: float | None) -> float:
    """Return the lowest W′ balance of a rider who sits in at one power the whole race.

    Args:
        draft: Power while sitting in, in watts.
        cp: Critical power, in watts.
        w_prime: W′, in joules.
        race_s: Race length in seconds, or ``None`` for a long race.

    Returns:
        ``w_prime`` when sitting in is below CP; otherwise what is left at the finish,
        ``-inf`` for a race with no end.

    """
    if draft <= cp:
        return w_prime
    if race_s is None:
        return -math.inf
    return w_prime - (draft - cp) * race_s
