"""The team on the route's real elevation profile, at a steady effort.

Everything else in the planner is worked out on level road: the target speed is a flat
speed. Here the team carries that effort over the route as Zwift lays it out, lead-in
included, using the elevation profile from the Zwift Speed Lab dataset. A typical rider
(the team's average weight and height) holds the power that gives the flat speed on every
slope, which sets the team's speed there; each rider's power to hold that speed, on the
front or sitting in, follows from the same power model. The finish time is the profile
ridden at those speeds.

``ttt_planner/help.html`` explains this to riders, so keep it in step with this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apps.ttt_planner.services import physics
from apps.zwift_data import catalog

if TYPE_CHECKING:
    from apps.zwift_data.models import ZwiftRoute

# A steeper slope is a glitch in the decoded profile, not a road (the Speed Lab clamps the same way).
GRADE_CLAMP = 0.35
# Each stretch's gradient is measured over about this distance, centred on it.
GRADE_WINDOW_M = 150
# Slopes this close together are timed as one, which keeps timing a course cheap enough
# to run inside a search over speeds.
_GRADE_BUCKET = 0.001
# The most points the course chart is sent.
CHART_POINTS = 240


@dataclass(frozen=True)
class Profile:
    """A route's elevation profile: distance from the start (m, lead-in included) and elevation (m)."""

    d: tuple[float, ...]
    e: tuple[float, ...]
    leadin_m: float

    @property
    def length_m(self) -> float:
        """Distance from the start to the finish, lead-in included."""
        return self.d[-1]


def load_profile(route: ZwiftRoute) -> Profile | None:
    """Read a route's profile from the Speed Lab catalog, cleaned up.

    Points that are not finite, or do not move forward, are dropped, and distances start
    at zero.

    Args:
        route: The route.

    Returns:
        The :class:`Profile`, or None when the catalog has no usable profile for the route.

    """
    raw = catalog.route_profile(route.world_id, route.name_hash)
    if not raw:
        return None
    points: list[tuple[float, float]] = []
    for d_raw, e_raw in zip(raw.get("d") or [], raw.get("e") or [], strict=False):
        try:
            d, e = float(d_raw), float(e_raw)
        except TypeError, ValueError:
            continue
        if not (math.isfinite(d) and math.isfinite(e)) or (points and d <= points[-1][0]):
            continue
        points.append((d, e))
    if len(points) < 2:
        return None
    start = points[0][0]
    length = points[-1][0] - start
    try:
        leadin = float(raw.get("leadin_m") or 0) - start
    except TypeError, ValueError:
        leadin = 0.0
    return Profile(
        d=tuple(d - start for d, _ in points),
        e=tuple(e for _, e in points),
        leadin_m=min(max(leadin, 0.0), length),
    )


def segment_grades(profile: Profile) -> list[float]:
    """Return the gradient (a fraction) of each stretch between two profile points.

    Each is the slope over about :data:`GRADE_WINDOW_M` centred on the stretch, not the
    stretch alone: the profile's elevations come in steps, and a rider's momentum carries
    them over a bump that short anyway. Clamped to :data:`GRADE_CLAMP`.

    Args:
        profile: The profile.

    Returns:
        One gradient per stretch, ``len(profile.d) - 1`` of them.

    """
    d, e = profile.d, profile.e
    last = len(d) - 1
    spacing = d[-1] / last if last else GRADE_WINDOW_M
    reach = max(0, round((GRADE_WINDOW_M / spacing - 1) / 2))
    grades = []
    for i in range(last):
        a, b = max(0, i - reach), min(last, i + 1 + reach)
        grades.append(min(max((e[b] - e[a]) / (d[b] - d[a]), -GRADE_CLAMP), GRADE_CLAMP))
    return grades


@dataclass(frozen=True)
class Course:
    """A route's profile with the typical rider who sets the team's pace on it."""

    profile: Profile
    grades: tuple[float, ...]
    weight_kg: float
    height_cm: float
    params: physics.PhysicsParams
    # Distance (m) at each bucketed gradient, for timing the course.
    _by_grade: tuple[tuple[float, float], ...]

    @classmethod
    def build(cls, profile: Profile, weight_kg: float, height_cm: float, params: physics.PhysicsParams) -> Course:
        """Prepare a profile for a team whose typical rider has this weight and height.

        Args:
            profile: The route's profile.
            weight_kg: The team's average weight.
            height_cm: The team's average height.
            params: Physics constants.

        Returns:
            The :class:`Course`.

        """
        grades = segment_grades(profile)
        lengths: dict[int, float] = {}
        for i, grade in enumerate(grades):
            key = round(grade / _GRADE_BUCKET)
            lengths[key] = lengths.get(key, 0.0) + profile.d[i + 1] - profile.d[i]
        return cls(
            profile=profile,
            grades=tuple(grades),
            weight_kg=weight_kg,
            height_cm=height_cm,
            params=params,
            _by_grade=tuple((key * _GRADE_BUCKET, metres) for key, metres in sorted(lengths.items())),
        )

    def team_power(self, flat_kph: float) -> float:
        """Return the typical rider's power to hold the flat speed on level road, alone.

        Args:
            flat_kph: The target flat speed.

        Returns:
            The power in watts, held on every slope.

        """
        return physics.power_for_speed(flat_kph, weight_kg=self.weight_kg, height_cm=self.height_cm, params=self.params)

    def speed(self, power_w: float, grade: float) -> float:
        """Return the team's speed on a gradient at the typical rider's steady power.

        Args:
            power_w: The steady power, from :meth:`team_power`.
            grade: The gradient, a fraction.

        Returns:
            The speed in km/h.

        """
        return physics.speed_on_grade(
            power_w, weight_kg=self.weight_kg, height_cm=self.height_cm, grade=grade, params=self.params
        )

    def seconds(self, flat_kph: float) -> float:
        """Return the time to ride the whole profile, lead-in included, at the steady effort for a flat speed.

        Args:
            flat_kph: The target flat speed.

        Returns:
            Seconds, or 0.0 when the speed is not positive.

        """
        if flat_kph <= 0:
            return 0.0
        power = self.team_power(flat_kph)
        total = 0.0
        for grade, metres in self._by_grade:
            kph = self.speed(power, grade)
            if kph <= 0:
                return math.inf
            total += metres / (kph / 3.6)
        return total


def chart_samples(course: Course, limit: int = CHART_POINTS) -> list[int]:
    """Pick the profile points the course chart shows, evenly spread and always including both ends.

    Args:
        course: The course.
        limit: The most points to pick.

    Returns:
        Indexes into the profile's points (each but the last also names the stretch after it).

    """
    last = len(course.profile.d) - 1
    if last + 1 <= limit:
        return list(range(last + 1))
    return sorted({round(k * last / (limit - 1)) for k in range(limit)})
