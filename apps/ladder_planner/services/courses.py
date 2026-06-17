"""Course / route helpers for the ladder planner.

Routes come from the shared ``ttt_planner.Route`` library, which carries distance
and elevation but no terrain type. ``derive_profile`` estimates a terrain profile
from climbing density (metres of climb per km) so picking a route can suggest the
``CourseProfile`` that drives the projected-score handicap. The suggestion stays
editable — it is only a prefill.
"""

from __future__ import annotations

from typing import Any

from apps.ttt_planner import terrain


def derive_profile(distance_km: float | None, elevation_m: float | None) -> str:
    """Estimate a terrain profile from climbing density (m/km).

    Delegates to ``ttt_planner.terrain`` (the single source of truth for the
    thresholds); the returned values match ``CourseProfile``.

    Args:
        distance_km: Route distance in km.
        elevation_m: Total elevation gain in metres.

    Returns:
        A ``CourseProfile`` value; defaults to ``ROLLING`` when distance is unknown.

    """
    return terrain.derive_terrain(distance_km, elevation_m)


def route_options() -> list[dict[str, Any]]:
    """Build the active-route list for the course picker, with a derived profile.

    Returns:
        A list of dicts with ``pk``, ``name``, ``label``, and ``profile`` (the
        derived ``CourseProfile`` value) per active route, ordered by name.

    """
    from apps.ttt_planner.models import Route  # local import to avoid app-load ordering issues

    options = []
    for route in Route.objects.filter(is_active=True):
        profile = derive_profile(float(route.distance_km), route.elevation_m)
        options.append({
            "pk": route.pk,
            "name": route.name,
            "label": f"{route.name} ({route.distance_km} km / {route.elevation_m} m)",
            "profile": profile,
        })
    return options
