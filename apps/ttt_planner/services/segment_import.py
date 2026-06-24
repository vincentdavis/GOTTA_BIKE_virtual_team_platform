"""Import Zwift segments from the bundled dataset.

Shared by the ``import_segments`` management command and the admin "Import
segments" button. Each dataset entry carries a ``type`` (Climb/Sprint/Segment),
``direction`` (Forward/Reverse), a distance (``distance_m`` or ``distance_km``),
optional ``grade_pct``/``category``, and a whatsonzwift ``url`` (the world is
derived from it). Segments are upserted on (name, world, direction), so importing
is idempotent.
"""

import json
from pathlib import Path

from apps.ttt_planner.models import Segment
from apps.ttt_planner.worlds import SLUG_TO_NAME

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "segments"

_TYPE_MAP = {
    "climb": Segment.SegmentType.CLIMB,
    "sprint": Segment.SegmentType.SPRINT,
    "segment": Segment.SegmentType.SEGMENT,
}
_DIRECTION_MAP = {"forward": Segment.Direction.FORWARD, "reverse": Segment.Direction.REVERSE}


def _world_from_url(url: str) -> str:
    """Derive the world display name from a whatsonzwift segment URL.

    Args:
        url: e.g. ``https://whatsonzwift.com/world/watopia/segment/...``.

    Returns:
        The world display name, a title-cased slug fallback, or "".

    """
    if "/world/" not in url:
        return ""
    slug = url.split("/world/", 1)[1].split("/", 1)[0]
    return SLUG_TO_NAME.get(slug, slug.replace("-", " ").title())


def _length_m(entry: dict) -> int:
    """Normalize an entry's distance to metres.

    Args:
        entry: A segment dict with ``distance_m`` or ``distance_km``.

    Returns:
        Length in metres (0 if neither is present).

    """
    if entry.get("distance_m") is not None:
        return round(entry["distance_m"])
    if entry.get("distance_km") is not None:
        return round(entry["distance_km"] * 1000)
    return 0


def import_segments(files: list[Path] | None = None) -> dict[str, int]:
    """Upsert Segment rows from the bundled (or given) JSON files.

    Args:
        files: Specific JSON files to import; defaults to every file in the
            bundled ``data/segments`` directory.

    Returns:
        Counts: ``{"created": int, "updated": int, "skipped": int}``.

    """
    paths = files if files is not None else sorted(DEFAULT_DATA_DIR.glob("*.json"))
    created = updated = skipped = 0
    for path in paths:
        for entry in json.loads(Path(path).read_text()):
            seg_type = _TYPE_MAP.get((entry.get("type") or "").lower())
            if seg_type is None:
                skipped += 1
                continue
            grade = entry.get("grade_pct")
            length_m = _length_m(entry)
            _, was_created = Segment.objects.update_or_create(
                name=entry["name"],
                world=_world_from_url(entry.get("url") or ""),
                direction=_DIRECTION_MAP.get((entry.get("direction") or "").lower(), ""),
                defaults={
                    "segment_type": seg_type,
                    "category": entry.get("category") or "",
                    "length_m": length_m,
                    "elevation_m": round(length_m * grade / 100) if grade and grade > 0 else 0,
                    "grade_pct": grade,
                    "whatsonzwift_url": entry.get("url") or "",
                },
            )
            created += was_created
            updated += not was_created
    return {"created": created, "updated": updated, "skipped": skipped}
