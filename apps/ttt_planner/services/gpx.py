"""Parse uploaded GPX tracks into distance / elevation / terrain.

Used when a ``RouteGpx`` file is uploaded so the stored record carries real
measured values (and a terrain type) rather than estimates.
"""

from __future__ import annotations

from dataclasses import dataclass

import gpxpy

from apps.ttt_planner import terrain


@dataclass
class GpxStats:
    """Parsed metrics from a GPX track."""

    distance_km: float
    elevation_m: int
    terrain: str
    point_count: int


def parse_gpx(content: str | bytes) -> GpxStats:
    """Parse GPX content into distance, elevation gain, terrain and point count.

    Args:
        content: Raw GPX file content (str or bytes).

    Returns:
        The parsed ``GpxStats``.

    Raises:
        ValueError: If the content is not parseable as GPX.

    """
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    try:
        gpx = gpxpy.parse(content)
    except Exception as exc:  # gpxpy raises various parse errors
        raise ValueError(f"Could not parse GPX: {exc}") from exc

    distance_m = gpx.length_2d() or 0.0
    uphill, _ = gpx.get_uphill_downhill()
    point_count = sum(len(seg.points) for track in gpx.tracks for seg in track.segments)

    distance_km = round(distance_m / 1000.0, 2)
    elevation_m = round(uphill or 0)
    return GpxStats(
        distance_km=distance_km,
        elevation_m=elevation_m,
        terrain=terrain.derive_terrain(distance_km, elevation_m),
        point_count=point_count,
    )
