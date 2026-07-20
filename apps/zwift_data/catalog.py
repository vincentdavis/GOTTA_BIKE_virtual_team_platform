"""Bucket-backed catalog for the large Zwift Speed Lab JSON files.

Route/segment *rows* live in the database (searchable, admin-editable); the bulk
geometry does not. ``route_profiles.json`` (~3.5 MB, ≤501-pt d/e/lat/lon per route) and
``segments.json`` (per-route crossing positions) are stored in object storage and parsed
into memory once per process, mirroring the source web app's in-memory catalog.

Cache coherence across web workers: a sync happens in the ``db_worker`` process, so an
in-process clear wouldn't reach the Granian workers. Instead each cached blob is stamped
with the ``ZwiftDataset.synced_at`` it was loaded for; a cheap PK read on access reloads
the blob only when a newer sync has landed.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

STORAGE_DIR = "zwift_data"
ROUTES_FILE = f"{STORAGE_DIR}/routes.json"
SEGMENTS_FILE = f"{STORAGE_DIR}/segments.json"
PROFILES_FILE = f"{STORAGE_DIR}/route_profiles.json"

_lock = threading.Lock()
# name -> (version_key, parsed_value). version_key is the dataset synced_at isoformat.
_cache: dict[str, tuple[str, Any]] = {}


def _version_key() -> str:
    """Return a cheap signal that changes when a new bundle is synced.

    Returns:
        The dataset ``synced_at`` isoformat, or ``""`` if never synced.

    """
    from .models import ZwiftDataset

    ts = ZwiftDataset.objects.filter(id=ZwiftDataset.SINGLETON_ID).values_list("synced_at", flat=True).first()
    return ts.isoformat() if ts else ""


def clear_cache() -> None:
    """Drop the in-process cache (call after a sync within the same process; tests)."""
    with _lock:
        _cache.clear()


def write_files(routes: bytes, segments: bytes, profiles: bytes) -> None:
    """Overwrite the three catalog JSON files in object storage.

    FileSystemStorage never overwrites (it renames), so delete-then-save to keep a stable
    path on every backend.
    """
    for path, data in ((ROUTES_FILE, routes), (SEGMENTS_FILE, segments), (PROFILES_FILE, profiles)):
        if default_storage.exists(path):
            default_storage.delete(path)
        default_storage.save(path, ContentFile(data))
    clear_cache()


def _load(name: str, path: str) -> Any:
    """Return parsed JSON for ``path``, cached until a newer sync lands.

    Returns:
        The parsed JSON, or ``{}`` when the file is missing.

    """
    version = _version_key()
    cached = _cache.get(name)
    if cached and cached[0] == version:
        return cached[1]
    with _lock:
        cached = _cache.get(name)
        if cached and cached[0] == version:
            return cached[1]
        try:
            with default_storage.open(path) as fh:
                value = json.loads(fh.read())
        except FileNotFoundError:
            value = {}
        _cache[name] = (version, value)
        return value


# ---- routes ---------------------------------------------------------------


def routes_document() -> dict:
    """Return the whole routes.json document.

    Returns:
        The ``{count, worlds, routes}`` document, or ``{}`` if unavailable.

    """
    return _load("routes", ROUTES_FILE) or {}


# ---- per-route elevation / GPS profiles -----------------------------------


def _profiles() -> dict[str, dict]:
    return (_load("profiles", PROFILES_FILE) or {}).get("profiles", {})


def route_profile(world_id: int, name_hash: str) -> dict | None:
    """Return the elevation + GPS profile for one route.

    Returns:
        The profile (d/e/lat/lon arrays + stats), or ``None`` if absent.

    """
    return _profiles().get(f"{world_id}:{name_hash}")


# ---- live segments (sprint / KOM / lap) -----------------------------------


def segments_document() -> dict:
    """Return the whole segments.json document.

    Returns:
        Every segment with its route crossings, or ``{}`` if unavailable.

    """
    return _load("segments", SEGMENTS_FILE) or {}


def _segments_by_route() -> dict[str, list[dict]]:
    """Return crossings keyed by ``"world_id:name_hash"`` in ride order.

    A lap segment recrosses one route, so each crossing is its own row.

    Returns:
        Mapping of route key to its ordered list of segment crossings.

    """
    out: dict[str, list[dict]] = {}
    for s in segments_document().get("segments", []):
        base = {
            "id": s["id"],
            "name": s.get("name"),
            "type": s.get("type"),
            "length_m": s.get("length_m"),
            "ascent_m": s.get("ascent_m"),
            "avg_grade_pct": s.get("avg_grade_pct"),
            "max_grade_pct": s.get("max_grade_pct"),
            "gives_powerup": s.get("gives_powerup", False),
        }
        for r in s.get("routes", []):
            key = f"{r['world_id']}:{r['name_hash']}"
            out.setdefault(key, []).append({
                **base,
                "pct_start": r.get("pct_start"),
                "pct_end": r.get("pct_end"),
                "start_m": r.get("start_m"),
                "end_m": r.get("end_m"),
                "span_m": r.get("span_m"),
            })
    for rows in out.values():
        rows.sort(key=lambda x: (x["start_m"] is None, x["start_m"] or 0.0))
    return out


def route_segments(world_id: int, name_hash: str) -> list[dict]:
    """Return the segments crossed by one route, in ride order.

    Returns:
        Ordered segment crossings (empty if the route has none).

    """
    return _segments_by_route().get(f"{world_id}:{name_hash}", [])


def segment_routes(segment_id: int) -> list[dict]:
    """Return the distinct routes that cross one segment.

    Returns:
        Route dicts (name/world_id/name_hash/position), empty if none.

    """
    for s in segments_document().get("segments", []):
        if int(s["id"]) != segment_id:
            continue
        seen: dict[tuple, dict] = {}
        for r in s.get("routes", []):
            key = (r["world_id"], r["name_hash"])
            if key not in seen:
                seen[key] = {
                    "name": r["name"],
                    "world_id": r["world_id"],
                    "name_hash": r["name_hash"],
                    "start_m": r.get("start_m"),
                    "pct_start": r.get("pct_start"),
                }
        return sorted(seen.values(), key=lambda r: r["name"].lower())
    return []
