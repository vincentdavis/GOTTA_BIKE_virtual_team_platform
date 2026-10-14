"""How hard a rotation is on one rider: Normalized Power and W′ balance.

Pure functions, no Django, so they can be tested on their own. Through a rotation a
rider's power is modelled as a square wave: ``front`` watts for their own pull, ``draft``
watts while sitting in for the rest of the rotation. Both measures are worked out in
closed form from that wave, which keeps them cheap enough for Calculate speed and
Auto-balance to evaluate thousands of times per click.

W′ balance follows the differential model (Skiba 2015, after Froncioni and Clarke).
Above critical power (CP), W′ drains at ``power - CP`` joules a second. Below it, W′
refills towards full at a rate set by how far below CP the rider is and how much is
missing: ``dW′bal/dt = (W′ - W′bal) * (CP - power) / W′``. Working with the deficit
``u = W′ - W′bal``, each stretch at constant power is an affine map on ``u``: draining
adds ``(power - CP) * seconds``, refilling multiplies by ``exp(-(CP - power) * seconds / W′)``.
"""

from __future__ import annotations

import math

# Normalized Power's rolling-average window, in seconds.
NP_WINDOW_S = 30


def cycle_np(front: float, draft: float, pull_s: float, cycle_s: float) -> float:
    """Return the Normalized Power of a repeating pull: ``front`` for ``pull_s``, ``draft`` for the rest.

    This is the steady state of a long race, so it does not depend on the race length.
    NP's 30-second rolling average of a square wave ramps linearly between the two levels
    for 30 seconds after each switch and is flat otherwise, so the mean of its fourth power
    has a closed form. A pull or a sit shorter than the window never reaches the other
    level; those are summed second by second over one rotation instead, wrapping round so
    the window looks back into the previous rotation.

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
    if pull_s >= cycle_s:
        return front
    window = NP_WINDOW_S
    sit_s = cycle_s - pull_s
    if front == draft:
        return front
    if pull_s >= window and sit_s >= window:
        # ∫x⁴ over one 30-second ramp of the rolling average from one level to the other.
        ramp = window * (front**5 - draft**5) / (5 * (front - draft))
        fourth = ((pull_s - window) * front**4 + (sit_s - window) * draft**4 + 2 * ramp) / cycle_s
        return fourth**0.25

    n = max(1, round(cycle_s))
    pull = min(max(1, round(pull_s)), n)
    # extended[j] is the power at second (j - window) of the rotation, wrapping round.
    extended = [front if ((j - window) % n) < pull else draft for j in range(n + window)]
    prefix = [0.0]
    for value in extended:
        prefix.append(prefix[-1] + value)
    fourth = sum(((prefix[t + window + 1] - prefix[t + 1]) / window) ** 4 for t in range(n)) / n
    return fourth**0.25


def _after(deficit: float, power: float, seconds: float, cp: float, w_prime: float) -> float:
    """Return the W′ deficit after ``seconds`` at ``power``.

    Args:
        deficit: W′ - W′bal before, in joules.
        power: Power held, in watts.
        seconds: How long it is held.
        cp: Critical power, in watts.
        w_prime: W′, in joules.

    Returns:
        The deficit afterwards, in joules (never below zero).

    """
    if seconds <= 0:
        return deficit
    if power > cp:
        return deficit + (power - cp) * seconds
    return deficit * math.exp(-(cp - power) * seconds / w_prime)


def lowest_w_prime_bal(
    front: float,
    draft: float,
    pull_s: float,
    cycle_s: float,
    cp: float,
    w_prime: float,
    pulls: int | None = None,
) -> float:
    """Return the lowest W′ balance a rider reaches over a race of repeating pulls.

    The race starts with W′ full. Each pull drains it (when the pull is above CP) and the
    sit that follows refills some of it, so the low point is the end of a pull, and it
    sinks a little with every rotation until the drain and the refill balance out.

    Args:
        front: Power on the front, in watts.
        draft: Power while sitting in, in watts.
        pull_s: Length of the rider's pull, in seconds.
        cycle_s: Length of the whole rotation, in seconds.
        cp: Critical power, in watts.
        w_prime: W′, in joules.
        pulls: How many pulls the rider takes in the race. ``None`` means a long race,
            where the pattern has settled: the lowest the balance can go.

    Returns:
        The lowest W′bal in joules: ``w_prime`` minus the largest deficit. Below zero when
        the rider runs out; ``-inf`` when a long race drains them without end.

    """
    sit_s = max(cycle_s - pull_s, 0.0)
    if pulls is not None:
        deficit = worst = 0.0
        for _ in range(max(pulls, 1)):
            deficit = _after(deficit, front, pull_s, cp, w_prime)
            worst = max(worst, deficit)
            deficit = _after(deficit, draft, sit_s, cp, w_prime)
            worst = max(worst, deficit)
        return w_prime - worst

    # A long race: the fixed point of one rotation, u -> sit(pull(u)). Each step is affine
    # in u, so the rotation is too: u -> scale * u + shift.
    def affine(power: float, seconds: float) -> tuple[float, float]:
        if seconds <= 0:
            return 1.0, 0.0
        if power > cp:
            return 1.0, (power - cp) * seconds
        return math.exp(-(cp - power) * seconds / w_prime), 0.0

    pull_scale, pull_shift = affine(front, pull_s)
    sit_scale, sit_shift = affine(draft, sit_s)
    scale = sit_scale * pull_scale
    shift = sit_scale * pull_shift + sit_shift
    if scale >= 1.0 - 1e-12:
        # Nothing ever refills: any drain at all runs W′ down without end.
        return -math.inf if shift > 1e-9 else w_prime
    start = shift / (1.0 - scale)  # deficit as each pull begins
    peak = max(start, pull_scale * start + pull_shift)
    return w_prime - peak


def sitting_w_prime_bal(draft: float, cp: float, w_prime: float, race_s: float | None) -> float:
    """Return the lowest W′ balance of a rider who sits in the whole race.

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
