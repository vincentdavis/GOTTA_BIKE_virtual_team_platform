"""Download the Zwift Speed Lab bundle and refresh the canonical dataset.

``sync_dataset()`` fetches ``/api/data/all.zip``, stores the three catalog JSON files in
object storage (see ``catalog.write_files``), and rebuilds the ``ZwiftWorld`` /
``ZwiftRoute`` / ``ZwiftSegment`` rows. Nothing FKs into these reference rows, so the
refresh is a plain delete-and-recreate inside one transaction.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

import httpx
import logfire
from constance import config
from django.db import transaction
from django.utils import timezone

from apps.zwift_data import catalog
from apps.zwift_data.models import ZwiftDataset, ZwiftRoute, ZwiftSegment, ZwiftWorld

BUNDLE_PATH = "/api/data/all.zip"
_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
_REQUIRED_MEMBERS = ("routes.json", "segments.json", "route_profiles.json")


@dataclass
class SyncResult:
    """Outcome of a sync — counts written and the bundle size."""

    worlds: int
    routes: int
    segments: int
    profiles: int
    bundle_bytes: int


def _base_url() -> str:
    return (config.ZWIFT_SPEED_LAB_URL or "https://zwiftspeedlab.coalitionracing.com").rstrip("/")


def _download() -> tuple[bytes, str]:
    """Fetch the bundle zip.

    Returns:
        Tuple of (zip bytes, the Last-Modified/ETag header value).

    """
    url = f"{_base_url()}{BUNDLE_PATH}"
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        stamp = resp.headers.get("last-modified") or resp.headers.get("etag") or ""
        return resp.content, stamp


def _upsert_worlds(routes: list[dict], segments: list[dict]) -> int:
    """Rebuild ZwiftWorld from the (world, world_id) pairs seen in the data.

    Returns:
        The number of worlds written.

    """
    worlds: dict[int, dict] = {}
    for r in routes:
        w = worlds.setdefault(r["world_id"], {"name": r["world"], "routes": 0, "segments": 0})
        w["routes"] += 1
    for s in segments:
        w = worlds.setdefault(s["world_id"], {"name": s["world"], "routes": 0, "segments": 0})
        w["segments"] += 1
    ZwiftWorld.objects.all().delete()
    ZwiftWorld.objects.bulk_create([
        ZwiftWorld(world_id=wid, name=w["name"], route_count=w["routes"], segment_count=w["segments"])
        for wid, w in worlds.items()
    ])
    return len(worlds)


def _upsert_routes(routes: list[dict]) -> int:
    """Upsert ZwiftRoute rows from the routes payload, preserving curated fields.

    Unlike worlds/segments, ZwiftRoute rows are FK targets (ladder + TTT planners) and
    carry curated data (vELO2 weights, recommended_laps), so this updates in place by
    ``name_hash`` — writing only ``SYNCED_FIELDS`` — and deletes routes that dropped out
    of the dataset, rather than delete-and-recreate.

    Returns:
        The number of routes in the payload.

    """
    seen: set[str] = set()
    for r in routes:
        name_hash = str(r["name_hash"])
        seen.add(name_hash)
        synced = {
            "name": r["name"],
            "world": r["world"],
            "sport": r.get("sport") or ZwiftRoute.Sport.CYCLING,
            "distance_km": r.get("distance_km") or 0.0,
            "ascent_m": r.get("ascent_m") or 0,
            "avg_gradient_pct": r.get("avg_gradient_pct") or 0.0,
            "leadin_km": r.get("leadin_km") or 0.0,
            "leadin_ascent_m": r.get("leadin_ascent_m") or 0,
            "supports_tt": bool(r.get("supports_tt")),
            "event_only": bool(r.get("event_only")),
            "level_locked": r.get("level_locked") or 0,
        }
        ZwiftRoute.objects.update_or_create(world_id=r["world_id"], name_hash=name_hash, defaults=synced)
    # Drop routes no longer present in the dataset (SET_NULL cascades to any FK refs).
    ZwiftRoute.objects.exclude(name_hash__in=seen).delete()
    return len(routes)


def _upsert_segments(segments: list[dict]) -> int:
    """Replace all ZwiftSegment rows from the segments payload.

    Returns:
        The number of segments written.

    """
    ZwiftSegment.objects.all().delete()
    ZwiftSegment.objects.bulk_create([
        ZwiftSegment(
            segment_id=int(s["id"]),
            name=s.get("name") or "",
            segment_type=s.get("type") or ZwiftSegment.SegmentType.SEGMENT,
            direction=(s.get("direction") or "") if s.get("direction") in ("Forward", "Reverse") else "",
            world=s["world"],
            world_id=s["world_id"],
            course_id=s.get("course_id") or 0,
            road_id=s.get("road_id") or 0,
            length_m=s.get("length_m") or 0,
            ascent_m=s.get("ascent_m") or 0.0,
            avg_grade_pct=s.get("avg_grade_pct") or 0.0,
            max_grade_pct=s.get("max_grade_pct") or 0.0,
            gives_powerup=bool(s.get("gives_powerup")),
            route_count=s.get("route_count") or len(s.get("routes", [])),
        )
        for s in segments
    ])
    return len(segments)


def sync_dataset() -> SyncResult:
    """Download the bundle, store the JSON files, and rebuild the DB rows.

    Returns:
        A :class:`SyncResult` with the counts written.

    Raises:
        ValueError: the bundle is missing a required file or is malformed.

    """
    dataset = ZwiftDataset.get()
    ZwiftDataset.objects.filter(id=dataset.id).update(syncing=True, last_error="")
    with logfire.span("zwift_data.sync_dataset", source=_base_url()):
        try:
            blob, stamp = _download()
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                names = set(zf.namelist())
                missing = [m for m in _REQUIRED_MEMBERS if m not in names]
                if missing:
                    raise ValueError(f"bundle missing files: {', '.join(missing)}")
                routes_bytes = zf.read("routes.json")
                segments_bytes = zf.read("segments.json")
                profiles_bytes = zf.read("route_profiles.json")

            routes_doc = json.loads(routes_bytes)
            segments_doc = json.loads(segments_bytes)
            profiles_doc = json.loads(profiles_bytes)
            routes = routes_doc.get("routes", [])
            segments = segments_doc.get("segments", [])
            if not routes or not segments:
                raise ValueError("bundle contained no routes or segments")

            # Store the geometry files first so the DB rows never point past missing files.
            catalog.write_files(routes_bytes, segments_bytes, profiles_bytes)

            with transaction.atomic():
                n_worlds = _upsert_worlds(routes, segments)
                n_routes = _upsert_routes(routes)
                n_segments = _upsert_segments(segments)
                n_profiles = len(profiles_doc.get("profiles", {}))
                ZwiftDataset.objects.filter(id=dataset.id).update(
                    source_url=f"{_base_url()}{BUNDLE_PATH}",
                    synced_at=timezone.now(),
                    bundle_last_modified=stamp,
                    bundle_bytes=len(blob),
                    routes_count=n_routes,
                    segments_count=n_segments,
                    worlds_count=n_worlds,
                    profiles_count=n_profiles,
                    last_error="",
                    syncing=False,
                )
            catalog.clear_cache()
            logfire.info(
                "zwift_data sync complete",
                worlds=n_worlds,
                routes=n_routes,
                segments=n_segments,
                profiles=n_profiles,
                bundle_bytes=len(blob),
            )
            return SyncResult(n_worlds, n_routes, n_segments, n_profiles, len(blob))
        except Exception as exc:
            logfire.error("zwift_data sync failed", error=str(exc))
            ZwiftDataset.objects.filter(id=dataset.id).update(syncing=False, last_error=str(exc)[:500])
            raise
