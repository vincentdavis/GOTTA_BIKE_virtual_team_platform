"""Normalize rider data from either a ``ZRRider`` row or the Zwift Racing API.

Both sources are flattened into one plain-dict shape (JSON-serializable) so the
comparison/scoring views in ``compute.py`` read a single consistent structure
regardless of whether a rider is on our team (``ZRRider``) or an opponent
(fetched live from the ZR rider/riders API).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.zwiftracing.models import ZRRider

# Power durations: (key used in the dict, human label, seconds).
# Keys match the ZR API/`ZRRider` field suffixes (w5/wkg5 ... w1200/wkg1200).
DURATIONS: list[tuple[str, str]] = [
    ("5", "5s"),
    ("15", "15s"),
    ("30", "30s"),
    ("60", "1m"),
    ("120", "2m"),
    ("300", "5m"),
    ("1200", "20m"),
]
DURATION_KEYS: list[str] = [k for k, _ in DURATIONS]

PROFILES: list[str] = ["flat", "rolling", "hilly", "mountainous"]

# vELO2 discipline scores: (key used in the dict, human label). Order matches the
# spreadsheet's vELO2 Scores block.
VELO_DISCIPLINES: list[tuple[str, str]] = [
    ("race", "Race Score"),
    ("endurance", "Endurance"),
    ("pursuit", "Pursuit"),
    ("sprint", "Sprint"),
    ("punch", "Punch"),
    ("climb", "Climb"),
    ("time_trial", "Time Trial"),
]
VELO_KEYS: list[str] = [k for k, _ in VELO_DISCIPLINES]
VELO_LABELS: list[str] = [label for _, label in VELO_DISCIPLINES]


def _f(value: Any) -> float | None:
    """Coerce a value (Decimal/str/number/None) to float or None.

    Args:
        value: The raw value.

    Returns:
        The float value, or None if missing/uncoercible.

    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _i(value: Any) -> int | None:
    """Coerce a value to int or None.

    Args:
        value: The raw value.

    Returns:
        The int value, or None if missing/uncoercible.

    """
    f = _f(value)
    return round(f) if f is not None else None


def _blank() -> dict[str, Any]:
    """Build an empty unified rider dict with all keys present.

    Returns:
        A unified rider dict with null/empty values.

    """
    return {
        "zwid": None,
        "name": "",
        "weight_kg": None,
        "height_cm": None,
        "zp_ftp": None,
        "zp_category": "",
        "phenotype": "",
        "rating_current": None,
        "rating_max30": None,
        "rating_max90": None,
        "rank": "",
        "finishes": 0,
        "podiums": 0,
        "wins": 0,
        "handicaps": dict.fromkeys(PROFILES),
        "velo": dict.fromkeys(VELO_KEYS),
        "w": dict.fromkeys(DURATION_KEYS),
        "wkg": dict.fromkeys(DURATION_KEYS),
    }


def from_zrrider(rider: ZRRider) -> dict[str, Any]:
    """Build the unified rider dict from a ``ZRRider`` model instance.

    Args:
        rider: The ZR rider row (our team, already synced locally).

    Returns:
        A JSON-serializable unified rider dict.

    """
    data = _blank()
    data["zwid"] = rider.zwid
    data["name"] = rider.name
    data["weight_kg"] = _f(rider.weight)
    data["height_cm"] = _i(rider.height)
    data["zp_ftp"] = _i(rider.zp_ftp)
    data["zp_category"] = rider.zp_category or ""
    data["phenotype"] = rider.phenotype_value or ""
    data["rating_current"] = _f(rider.race_current_rating)
    data["rating_max30"] = _f(rider.race_max30_rating)
    data["rating_max90"] = _f(rider.race_max90_rating)
    data["rank"] = rider.race_current_category or ""
    data["finishes"] = rider.race_finishes or 0
    data["podiums"] = rider.race_podiums or 0
    data["wins"] = rider.race_wins or 0
    data["handicaps"] = {
        "flat": _f(rider.handicap_flat),
        "rolling": _f(rider.handicap_rolling),
        "hilly": _f(rider.handicap_hilly),
        "mountainous": _f(rider.handicap_mountainous),
    }
    data["velo"] = {
        "race": _f(rider.velo_race),
        "endurance": _f(rider.velo_endurance),
        "pursuit": _f(rider.velo_pursuit),
        "sprint": _f(rider.velo_sprint),
        "punch": _f(rider.velo_punch),
        "climb": _f(rider.velo_climb),
        "time_trial": _f(rider.velo_time_trial),
    }
    for key in DURATION_KEYS:
        data["w"][key] = _i(getattr(rider, f"power_w{key}", None))
        data["wkg"][key] = _f(getattr(rider, f"power_wkg{key}", None))
    return data


def from_api(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the unified rider dict from a Zwift Racing API rider payload.

    Handles the nested shape returned by ``zr_client.get_rider`` /
    ``get_riders`` (keys: ``riderId``, ``power``, ``race``, ``handicaps``,
    ``phenotype`` ...).

    Args:
        payload: A single rider object from the ZR API.

    Returns:
        A JSON-serializable unified rider dict.

    """
    data = _blank()
    data["zwid"] = _i(payload.get("riderId"))
    data["name"] = payload.get("name") or ""
    data["weight_kg"] = _f(payload.get("weight"))
    data["height_cm"] = _i(payload.get("height"))
    data["zp_ftp"] = _i(payload.get("zpFTP"))
    data["zp_category"] = payload.get("zpCategory") or ""

    power = payload.get("power") or {}
    for key in DURATION_KEYS:
        data["w"][key] = _i(power.get(f"w{key}"))
        data["wkg"][key] = _f(power.get(f"wkg{key}"))

    race = payload.get("race") or {}
    current = race.get("current") or {}
    data["rating_current"] = _f(current.get("rating"))
    data["rating_max30"] = _f((race.get("max30") or {}).get("rating"))
    data["rating_max90"] = _f((race.get("max90") or {}).get("rating"))
    data["rank"] = (current.get("mixed") or {}).get("category") or ""
    data["finishes"] = _i(race.get("finishes")) or 0
    data["podiums"] = _i(race.get("podiums")) or 0
    data["wins"] = _i(race.get("wins")) or 0

    profile = (payload.get("handicaps") or {}).get("profile") or {}
    data["handicaps"] = {p: _f(profile.get(p)) for p in PROFILES}

    velo = payload.get("velo") or {}
    velo_factors = velo.get("factors") or {}
    data["velo"] = {
        "race": _f(velo.get("race")),
        "endurance": _f(velo_factors.get("endurance")),
        "pursuit": _f(velo_factors.get("pursuit")),
        "sprint": _f(velo_factors.get("sprint")),
        "punch": _f(velo_factors.get("punch")),
        "climb": _f(velo_factors.get("climb")),
        "time_trial": _f(velo.get("timeTrial")),
    }

    data["phenotype"] = (payload.get("phenotype") or {}).get("value") or ""
    return data
