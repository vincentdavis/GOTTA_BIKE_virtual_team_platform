"""Scoring and comparison computations for a ladder matchup.

All functions read the frozen ``zr_data`` snapshot on each ``LadderRider`` (see
``normalize``), never live data, so a saved matchup is reproducible. Only riders
with ``is_racing=True`` are included.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from apps.ladder_planner.models import Side
from apps.ladder_planner.services.normalize import DURATION_KEYS, DURATIONS, VELO_KEYS, VELO_LABELS

if TYPE_CHECKING:
    from apps.ladder_planner.models import LadderMatchup, LadderRider

DURATION_LABELS: list[str] = [label for _, label in DURATIONS]

# Heatmap endpoints: worst (low) red -> best (high) green.
_LOW_RGB = (230, 124, 115)
_HIGH_RGB = (122, 201, 160)


def _labels(matchup: LadderMatchup) -> tuple[str, str]:
    """Resolve display labels for the two sides.

    Args:
        matchup: The matchup.

    Returns:
        Tuple of (our label, opponent label).

    """
    return (matchup.our_team_name or "Our team", matchup.opponent_team_name or "Opponent")


def _racing(matchup: LadderMatchup) -> tuple[list[LadderRider], list[LadderRider]]:
    """Split a matchup's racing riders into (ours, opponents).

    Args:
        matchup: The matchup.

    Returns:
        Tuple of (our riders, opponent riders), each ordered.

    """
    riders = [r for r in matchup.riders.all() if r.is_racing]
    ours = [r for r in riders if r.side == Side.OURS]
    opp = [r for r in riders if r.side == Side.OPPONENT]
    return ours, opp


def _round_w(value: float | None) -> int | None:
    """Round a raw-watts value to int.

    Args:
        value: The value or None.

    Returns:
        Rounded int, or None.

    """
    return round(value) if value is not None else None


def _round_wkg(value: float | None) -> float | None:
    """Round a w/kg value to one decimal.

    Args:
        value: The value or None.

    Returns:
        Rounded float, or None.

    """
    return round(value, 1) if value is not None else None


# ----- Projected score ---------------------------------------------------------------------------


def projected_score(matchup: LadderMatchup) -> dict[str, Any]:
    """Rank all racing riders by course-handicapped vELO and project a team score.

    Handicapped rating = ``rating_current + handicap[course_profile]`` (a missing
    handicap counts as 0). Riders sort highest-first; finish positions run 1..N
    and points run N..1 (1st place earns N points). Each side's points are summed;
    the higher total is the favoured team.

    Args:
        matchup: The matchup.

    Returns:
        A dict with ``rows`` (ranked riders), per-side ``our_points`` /
        ``opp_points`` totals, ``favored`` side label, and team labels.

    """
    our_label, opp_label = _labels(matchup)
    profile = matchup.course_profile
    ours, opp = _racing(matchup)

    entries: list[dict[str, Any]] = []
    for side, label, riders in ((Side.OURS, our_label, ours), (Side.OPPONENT, opp_label, opp)):
        for rider in riders:
            data = rider.zr_data or {}
            rating = data.get("rating_current")
            if rating is None:
                continue
            handicap = (data.get("handicaps") or {}).get(profile) or 0.0
            entries.append({
                "name": rider.name,
                "side": side,
                "team": label,
                "rating": round(rating, 1),
                "handicap": round(handicap, 1),
                "handicapped": round(rating + handicap, 1),
                # Fields consumed by the user tooltip (links + badges).
                "zwid": rider.zwid,
                "zp_category": data.get("zp_category") or "",
                "zr_category": data.get("rank") or "",
                "zr_rating": round(rating, 1),
                "zr_phenotype": data.get("phenotype") or "",
            })

    entries.sort(key=lambda e: e["handicapped"], reverse=True)
    total = len(entries)
    our_points = opp_points = 0
    for finish, entry in enumerate(entries, start=1):
        entry["finish"] = finish
        entry["points"] = total - finish + 1
        if entry["side"] == Side.OURS:
            our_points += entry["points"]
        else:
            opp_points += entry["points"]

    if our_points > opp_points:
        favored = our_label
    elif opp_points > our_points:
        favored = opp_label
    else:
        favored = None

    return {
        "rows": entries,
        "our_label": our_label,
        "opp_label": opp_label,
        "our_points": our_points,
        "opp_points": opp_points,
        "favored": favored,
        "profile": matchup.get_course_profile_display(),
    }


# ----- Power comparison --------------------------------------------------------------------------


def _values(riders: list[LadderRider], domain: str, key: str) -> list[float]:
    """Collect non-null power values for a domain/duration across riders.

    Args:
        riders: The riders.
        domain: ``"w"`` or ``"wkg"``.
        key: Duration key (e.g. ``"60"``).

    Returns:
        List of present float values.

    """
    out: list[float] = []
    for rider in riders:
        value = (rider.zr_data.get(domain) or {}).get(key)
        if value is not None:
            out.append(float(value))
    return out


_AGGREGATORS = {
    "Average": statistics.mean,
    "Max": max,
    "Min": min,
    "Median": statistics.median,
}


def power_comparison(matchup: LadderMatchup) -> dict[str, Any]:
    """Build Average/Max/Min/Median power comparison tables and chart series.

    For each domain (w/kg, raw watts) and metric, computes a per-duration value
    for each side plus the advantage (ours - opponent), matching the spreadsheet's
    team-comparison blocks.

    Args:
        matchup: The matchup.

    Returns:
        A dict with ``durations``, ``tables`` (per domain), ``charts`` (average
        and median series for both domains), and team labels.

    """
    our_label, opp_label = _labels(matchup)
    ours, opp = _racing(matchup)
    rounder = {"w": _round_w, "wkg": _round_wkg}

    tables = []
    charts: dict[str, Any] = {}
    for domain, domain_label in (("wkg", "Power: w/kg"), ("w", "Power: Raw Watts")):
        round_fn = rounder[domain]
        metrics = []
        for metric_label, agg in _AGGREGATORS.items():
            our_row, opp_row, adv_row = [], [], []
            for key in DURATION_KEYS:
                our_vals = _values(ours, domain, key)
                opp_vals = _values(opp, domain, key)
                our_v = agg(our_vals) if our_vals else None
                opp_v = agg(opp_vals) if opp_vals else None
                adv = (our_v - opp_v) if (our_v is not None and opp_v is not None) else None
                our_row.append(round_fn(our_v))
                opp_row.append(round_fn(opp_v))
                adv_row.append(round_fn(adv))
            metrics.append({"label": metric_label, "ours": our_row, "opp": opp_row, "adv": adv_row})
            if metric_label in ("Average", "Median"):
                charts[f"{domain}_{metric_label.lower()}"] = {
                    "ours": [v if v is not None else 0 for v in our_row],
                    "opp": [v if v is not None else 0 for v in opp_row],
                }
        tables.append({"domain": domain, "domain_label": domain_label, "metrics": metrics})

    return {
        "durations": DURATION_LABELS,
        "tables": tables,
        "charts": charts,
        "our_label": our_label,
        "opp_label": opp_label,
    }


# ----- Top riders --------------------------------------------------------------------------------


def top_riders(matchup: LadderMatchup) -> list[dict[str, Any]]:
    """Find the best rider (across both sides) for each duration and domain.

    Args:
        matchup: The matchup.

    Returns:
        A list of groups (one per domain), each with the best rider per duration.

    """
    our_label, opp_label = _labels(matchup)
    riders = [(r, our_label) for r in _racing(matchup)[0]] + [(r, opp_label) for r in _racing(matchup)[1]]

    groups = []
    for domain, domain_label, round_fn in (("wkg", "Power: w/kg", _round_wkg), ("w", "Power: Raw Watts", _round_w)):
        rows = []
        for key, label in DURATIONS:
            best_rider = None
            best_label = ""
            best_val = None
            for rider, team_label in riders:
                value = (rider.zr_data.get(domain) or {}).get(key)
                if value is not None and (best_val is None or value > best_val):
                    best_val = float(value)
                    best_rider = rider
                    best_label = team_label
            if best_rider is not None:
                rows.append({
                    "duration": label,
                    "name": best_rider.name,
                    "team": best_label,
                    "side": best_rider.side,
                    "value": round_fn(best_val),
                })
        groups.append({"domain_label": domain_label, "rows": rows})
    return groups


# ----- Per-rider power table (heatmap) -----------------------------------------------------------


def _intensity_rgb(value: float, lo: float, hi: float) -> str:
    """Blend a value's position in [lo, hi] from red (low) to green (high).

    Args:
        value: The cell value.
        lo: Column minimum.
        hi: Column maximum.

    Returns:
        A ``"rgb(r,g,b)"`` string for an inline background style.

    """
    intensity = 0.5 if hi <= lo else (value - lo) / (hi - lo)
    r = round(_LOW_RGB[0] + (_HIGH_RGB[0] - _LOW_RGB[0]) * intensity)
    g = round(_LOW_RGB[1] + (_HIGH_RGB[1] - _LOW_RGB[1]) * intensity)
    b = round(_LOW_RGB[2] + (_HIGH_RGB[2] - _LOW_RGB[2]) * intensity)
    return f"rgb({r},{g},{b})"


def per_rider_power(matchup: LadderMatchup) -> dict[str, Any]:
    """Build a per-rider power table with per-column heatmap shading.

    Args:
        matchup: The matchup.

    Returns:
        A dict with ``durations`` and ``rows``; each row has ``wkg`` and ``w``
        cell lists carrying the display value and an ``rgb`` background string.

    """
    our_label, opp_label = _labels(matchup)
    ours, opp = _racing(matchup)
    riders = [(r, our_label) for r in ours] + [(r, opp_label) for r in opp]

    # Column min/max per domain+duration for the heatmap scale.
    bounds: dict[tuple[str, str], tuple[float, float]] = {}
    for domain in ("wkg", "w"):
        for key in DURATION_KEYS:
            vals = [float(v) for rider, _ in riders if (v := (rider.zr_data.get(domain) or {}).get(key)) is not None]
            if vals:
                bounds[domain, key] = (min(vals), max(vals))

    rounder = {"w": _round_w, "wkg": _round_wkg}
    rows = []
    for rider, team_label in riders:
        cells = {"name": rider.name, "team": team_label, "side": rider.side}
        for domain in ("wkg", "w"):
            domain_cells = []
            for key in DURATION_KEYS:
                raw = (rider.zr_data.get(domain) or {}).get(key)
                if raw is None:
                    domain_cells.append({"value": None, "rgb": ""})
                    continue
                lo, hi = bounds.get((domain, key), (float(raw), float(raw)))
                domain_cells.append({"value": rounder[domain](float(raw)), "rgb": _intensity_rgb(float(raw), lo, hi)})
            cells[domain] = domain_cells
        rows.append(cells)

    return {"durations": DURATION_LABELS, "rows": rows, "our_label": our_label, "opp_label": opp_label}


# ----- vELO2 discipline scores -------------------------------------------------------------------


def _round_score(value: float | None) -> int | None:
    """Round a vELO2 discipline score to int.

    Args:
        value: The value or None.

    Returns:
        Rounded int, or None.

    """
    return round(value) if value is not None else None


def _velo_values(riders: list[LadderRider], key: str) -> list[float]:
    """Collect non-null vELO2 discipline values across riders.

    Args:
        riders: The riders.
        key: Discipline key (e.g. ``"sprint"``).

    Returns:
        List of present float values.

    """
    out: list[float] = []
    for rider in riders:
        value = (rider.zr_data.get("velo") or {}).get(key)
        if value is not None:
            out.append(float(value))
    return out


def velo2_comparison(matchup: LadderMatchup) -> dict[str, Any]:
    """Build the per-rider vELO2 discipline table and a per-discipline comparison.

    Each per-rider cell carries a red->green heatmap shade scaled within its
    discipline column. The comparison block reports Median/Average/Minimum/Maximum
    per discipline for each side plus the advantage (ours - opponent).

    Args:
        matchup: The matchup.

    Returns:
        A dict with ``disciplines``, per-rider ``rows`` (heatmap cells + rank),
        comparison ``metrics``, and team labels.

    """
    our_label, opp_label = _labels(matchup)
    ours, opp = _racing(matchup)
    riders = [(r, our_label) for r in ours] + [(r, opp_label) for r in opp]

    bounds: dict[str, tuple[float, float]] = {}
    for key in VELO_KEYS:
        vals = [float(v) for rider, _ in riders if (v := (rider.zr_data.get("velo") or {}).get(key)) is not None]
        if vals:
            bounds[key] = (min(vals), max(vals))

    rows = []
    for rider, team_label in riders:
        velo = rider.zr_data.get("velo") or {}
        cells = []
        for key in VELO_KEYS:
            raw = velo.get(key)
            if raw is None:
                cells.append({"value": None, "rgb": ""})
                continue
            lo, hi = bounds.get(key, (float(raw), float(raw)))
            cells.append({"value": _round_score(float(raw)), "rgb": _intensity_rgb(float(raw), lo, hi)})
        rows.append({
            "name": rider.name,
            "team": team_label,
            "side": rider.side,
            "rank": rider.zr_data.get("rank") or "",
            "cells": cells,
        })

    order = (("Median", statistics.median), ("Average", statistics.mean), ("Minimum", min), ("Maximum", max))
    metrics = []
    for label, agg in order:
        our_row, opp_row, adv_row = [], [], []
        for key in VELO_KEYS:
            ov, pv = _velo_values(ours, key), _velo_values(opp, key)
            o = agg(ov) if ov else None
            p = agg(pv) if pv else None
            adv = (o - p) if (o is not None and p is not None) else None
            our_row.append(_round_score(o))
            opp_row.append(_round_score(p))
            adv_row.append(_round_score(adv))
        metrics.append({"label": label, "ours": our_row, "opp": opp_row, "adv": adv_row})

    return {
        "disciplines": VELO_LABELS,
        "rows": rows,
        "metrics": metrics,
        "our_label": our_label,
        "opp_label": opp_label,
    }


# ----- Other stats -------------------------------------------------------------------------------


def other_stats(matchup: LadderMatchup) -> dict[str, Any]:
    """Build the per-rider "other stats" table (size, zFTP, category/rank, zRapp).

    Args:
        matchup: The matchup.

    Returns:
        A dict with per-rider ``rows`` and team labels.

    """
    our_label, opp_label = _labels(matchup)
    ours, opp = _racing(matchup)
    riders = [(r, our_label) for r in ours] + [(r, opp_label) for r in opp]

    rows = []
    for rider, team_label in riders:
        d = rider.zr_data
        weight = d.get("weight_kg")
        height = d.get("height_cm")
        zftp = d.get("zp_ftp")
        finishes = d.get("finishes") or 0
        rows.append({
            "name": rider.name,
            "team": team_label,
            "side": rider.side,
            "weight_kg": _round_wkg(weight),
            "weight_lb": round(weight * 2.20462) if weight else None,
            "height_cm": height,
            "height_ft": round(height / 30.48, 2) if height else None,
            "zftp": zftp,
            "zftp_wkg": round(zftp / weight, 1) if (zftp and weight) else None,
            "category": d.get("zp_category") or "",
            "rank": d.get("rank") or "",
            "podium_pct": round(100 * (d.get("podiums") or 0) / finishes) if finishes else None,
            "phenotype": d.get("phenotype") or "",
            "zrapp_curr": _round_w(d.get("rating_current")),
            "zrapp_30": _round_w(d.get("rating_max30")),
            "zrapp_90": _round_w(d.get("rating_max90")),
        })
    return {"rows": rows, "our_label": our_label, "opp_label": opp_label}


def matchup_summary(matchup: LadderMatchup) -> dict[str, Any]:
    """Bundle all computed views for a matchup detail page.

    Args:
        matchup: The matchup.

    Returns:
        A dict of all view payloads plus rider counts.

    """
    ours, opp = _racing(matchup)
    return {
        "projected": projected_score(matchup),
        "power": power_comparison(matchup),
        "top": top_riders(matchup),
        "per_rider": per_rider_power(matchup),
        "velo2": velo2_comparison(matchup),
        "other": other_stats(matchup),
        "our_count": len(ours),
        "opp_count": len(opp),
    }
