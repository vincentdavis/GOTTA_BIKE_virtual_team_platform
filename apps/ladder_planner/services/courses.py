"""Course / route helpers for the ladder planner.

Routes come from the canonical ``zwift_data.ZwiftRoute`` library, which carries
distance and elevation but no terrain type. ``derive_profile`` estimates a terrain
profile from climbing density (metres of climb per km) so picking a route can suggest
the ``CourseProfile`` that drives the projected-score handicap. The suggestion stays
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
    """Build the cycling-route list for the course picker, with a derived profile.

    Reads the canonical :class:`~apps.zwift_data.models.ZwiftRoute` dataset.

    Returns:
        A list of dicts with ``pk``, ``name``, ``label``, and ``profile`` (the
        derived ``CourseProfile`` value) per cycling route, ordered by world then name.

    """
    from apps.zwift_data.models import ZwiftRoute  # local import to avoid app-load ordering issues

    options = []
    for route in ZwiftRoute.objects.filter(sport=ZwiftRoute.Sport.CYCLING):
        profile = derive_profile(float(route.distance_km), route.ascent_m)
        options.append({
            "pk": route.pk,
            "name": route.name,
            "label": f"{route.name} — {route.world} ({route.distance_km:g} km / {route.ascent_m} m)",
            "profile": profile,
        })
    return options
