"""Map a TttPlan to a zwiftgopher optimize request and normalize the response."""

from __future__ import annotations

from typing import TYPE_CHECKING

VALID_ROUTE_SCHEDULES = ("next", "next_wtrl", "next_zrl")
DEFAULT_ROUTE_SCHEDULE = "next_wtrl"
MIN_RIDERS = 2
MAX_RIDERS = 8

if TYPE_CHECKING:
    from apps.ttt_planner.models import TttPlan


def build_optimize_request(plan: TttPlan, route_schedule: str = DEFAULT_ROUTE_SCHEDULE) -> dict:
    """Build the optimize request body for a plan.

    Riders with a zwid are sent as IDs plus ``rider_overrides`` carrying our
    snapshotted weight/FTP/height so the optimizer uses the same numbers we do.
    Manual riders (no zwid) are sent as ``custom_riders`` only when they have all
    required fields (name, ftp, weight, height).

    Args:
        plan: The plan to optimize.
        route_schedule: One of ``next`` / ``next_wtrl`` / ``next_zrl``.

    Returns:
        The request body dict.

    """
    if route_schedule not in VALID_ROUTE_SCHEDULES:
        route_schedule = DEFAULT_ROUTE_SCHEDULE

    riders: list[int] = []
    rider_overrides: dict[str, dict] = {}
    custom_riders: list[dict] = []

    for r in plan.riders.all():
        if r.zwid:
            riders.append(r.zwid)
            override = {"name": r.name}
            if r.ftp_w:
                override["ftp"] = r.ftp_w
            if r.weight_kg is not None:
                override["weight"] = float(r.weight_kg)
            if r.height_cm is not None:
                override["height"] = r.height_cm
            rider_overrides[str(r.zwid)] = override
        elif r.ftp_w and r.weight_kg is not None and r.height_cm is not None:
            custom_riders.append({"name": r.name, "ftp": r.ftp_w, "weight": float(r.weight_kg), "height": r.height_cm})

    payload: dict = {
        "request_id": str(plan.pk),
        "team_name": plan.team_name or plan.name or "Coalition TTT",
        "route": route_schedule,
        "target_speed": float(plan.target_speed_kph),
    }
    if riders:
        payload["riders"] = riders
        if rider_overrides:
            payload["rider_overrides"] = rider_overrides
    if custom_riders:
        payload["custom_riders"] = custom_riders
    return payload


def count_optimizable_riders(plan: TttPlan) -> int:
    """Count riders that will be sent to the optimizer.

    Args:
        plan: The plan.

    Returns:
        Number of riders with a zwid or a complete manual profile.

    """
    n = 0
    for r in plan.riders.all():
        if r.zwid or (r.ftp_w and r.weight_kg is not None and r.height_cm is not None):
            n += 1
    return n


def parse_optimize_response(status_code: int, data: dict) -> dict:
    """Normalize a single optimize response into a stored result dict.

    Args:
        status_code: HTTP status from the client (0 = transport error).
        data: Parsed JSON body.

    Returns:
        A dict with ``ok`` plus either the result fields or an ``error`` string.

    """
    if status_code == 0:
        return {"ok": False, "error": data.get("error", "request failed")}
    if status_code == 429:
        return {"ok": False, "error": "Rate limited by zwiftgopher (1 request/min). Try again shortly."}
    if status_code >= 400 or not data.get("success"):
        message = data.get("message") or data.get("error") or f"HTTP {status_code}"
        return {"ok": False, "error": str(message)[:300]}

    payload = data.get("data") or {}
    riders = payload.get("riders") or []
    riders = sorted(riders, key=lambda r: r.get("order") or 0)
    return {
        "ok": True,
        "route": payload.get("route"),
        "distance_km": payload.get("distance_km"),
        "elevation_m": payload.get("elevation_m"),
        "estimated_time_seconds": payload.get("estimated_time_seconds"),
        "estimated_time_formatted": payload.get("estimated_time_formatted"),
        "estimated_avg_speed": payload.get("estimated_avg_speed"),
        "team_avg_power": payload.get("team_avg_power"),
        "team_avg_if": payload.get("team_avg_if"),
        "riders": riders,
    }
