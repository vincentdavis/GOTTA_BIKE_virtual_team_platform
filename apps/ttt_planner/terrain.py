"""Terrain-type derivation for routes, shared by the TTT and ladder planners.

Routes (``ttt_planner.Route``) carry distance and elevation but no terrain type.
``derive_terrain`` estimates a type from climbing density (metres of climb per km).
This module is the single source of truth for the thresholds; the ladder planner's
``CourseProfile`` uses the same string values and delegates here.
"""

from __future__ import annotations

from typing import Any

# (value, label) — values match apps.ladder_planner.models.CourseProfile.
TERRAIN_CHOICES: list[tuple[str, str]] = [
    ("flat", "Flat"),
    ("rolling", "Rolling"),
    ("hilly", "Hilly"),
    ("mountainous", "Mountainous"),
]
TERRAIN_VALUES: set[str] = {value for value, _ in TERRAIN_CHOICES}

# Climbing-density thresholds (metres of climb per km) → terrain type.
_FLAT_MAX = 8.0
_ROLLING_MAX = 15.0
_HILLY_MAX = 25.0


def derive_terrain(distance_km: float | None, elevation_m: float | None) -> str:
    """Estimate a terrain type from climbing density (m/km).

    Args:
        distance_km: Route distance in km.
        elevation_m: Total elevation gain in metres.

    Returns:
        A terrain value (``flat`` / ``rolling`` / ``hilly`` / ``mountainous``);
        defaults to ``rolling`` when distance is unknown.

    """
    if not distance_km or distance_km <= 0:
        return "rolling"
    m_per_km = (elevation_m or 0) / float(distance_km)
    if m_per_km < _FLAT_MAX:
        return "flat"
    if m_per_km < _ROLLING_MAX:
        return "rolling"
    if m_per_km < _HILLY_MAX:
        return "hilly"
    return "mountainous"


def terrain_label(value: str) -> str:
    """Return the human label for a terrain value.

    Args:
        value: A terrain value.

    Returns:
        The display label, or empty string if unknown.

    """
    return dict(TERRAIN_CHOICES).get(value, "")


def route_options() -> list[dict[str, Any]]:
    """Build the active-route list for course pickers, with a derived terrain type.

    Returns:
        A list of dicts with ``pk``, ``name``, ``label``, and ``terrain`` per
        active route, ordered by name.

    """
    from apps.ttt_planner.models import Route  # local import to avoid app-load ordering issues

    return [
        {
            "pk": route.pk,
            "name": route.name,
            "label": f"{route.name} ({route.distance_km} km / {route.elevation_m} m)",
            "terrain": derive_terrain(float(route.distance_km), route.elevation_m),
        }
        for route in Route.objects.filter(is_active=True)
    ]
