"""Import ZwiftRacing vELO2 "Race" factor weights onto canonical routes.

The ZwiftRacing routes reference (``apps/zwiftracing/docs/ZwiftRacing Routes VELO
WEIGHTS.json``) is a list keyed by ``routeId`` — which is exactly our canonical
``ZwiftRoute.name_hash`` — so weights join to routes by id, not by fuzzy name match, and
survive a dataset re-sync. Each entry's ``velo.race`` block holds the five Race factors
(plus Time Trial Speed, which we drop — it feeds the TT rating, not Race) as fractions
summing to ~1.0; we store them as percent (x100) to mirror the planner's expectations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import logfire

from apps.zwift_data.models import ZwiftRoute

# The reference JSON shipped in the repo (default source for the import).
# velo.py is at apps/zwift_data/services/ -> parents[2] is the apps/ dir.
DEFAULT_VELO_FILE = (
    Path(__file__).resolve().parents[2] / "zwiftracing" / "docs" / "ZwiftRacing Routes VELO WEIGHTS.json"
)

# JSON velo.race key -> ZwiftRoute field suffix. Time Trial Speed is intentionally omitted.
_FACTOR_MAP = {
    "sprint": "sprint",
    "punch": "punch",
    "climb": "climb",
    "endurance": "endurance",
    "pursuit": "pursuit",
}


@dataclass
class VeloImportResult:
    """Outcome of a vELO import."""

    updated: int
    unmatched: int
    total: int
    unmatched_names: list[str]


def _pct(value: object) -> Decimal:
    """Convert a 0-1 fraction to a 0-100 percent Decimal, rounded to 2 dp.

    Returns:
        The value as a percent Decimal.

    """
    return round(Decimal(str(value or 0)) * 100, 2)


def import_velo_weights(data: list[dict]) -> VeloImportResult:
    """Write vELO2 Race weights from parsed JSON onto ZwiftRoute rows, matched by name_hash.

    Args:
        data: The parsed ZwiftRacing routes list (each entry has ``routeId`` + ``velo``).

    Returns:
        A :class:`VeloImportResult` with counts and the names that didn't match a route.

    """
    by_hash = {r.name_hash: r for r in ZwiftRoute.objects.all()}
    updated = 0
    unmatched_names: list[str] = []
    for entry in data:
        route = by_hash.get(str(entry.get("routeId")))
        if route is None:
            unmatched_names.append(str(entry.get("name") or entry.get("routeId")))
            continue
        race = (entry.get("velo") or {}).get("race") or {}
        for json_key, suffix in _FACTOR_MAP.items():
            setattr(route, f"velo_{suffix}", _pct(race.get(json_key)))
        num_events = entry.get("numEvents")
        route.velo_num_events = int(num_events) if num_events is not None else None
        route.save(update_fields=[f"velo_{s}" for s in _FACTOR_MAP.values()] + ["velo_num_events"])
        updated += 1
    logfire.info("vELO weights imported", updated=updated, unmatched=len(unmatched_names), total=len(data))
    return VeloImportResult(
        updated=updated, unmatched=len(unmatched_names), total=len(data), unmatched_names=unmatched_names
    )


def import_velo_from_file(path: Path | str | None = None) -> VeloImportResult:
    """Import vELO2 weights from a JSON file (defaults to the bundled reference).

    Args:
        path: Path to the ZwiftRacing routes JSON; defaults to :data:`DEFAULT_VELO_FILE`.

    Returns:
        A :class:`VeloImportResult`.

    Raises:
        ValueError: the file is not a JSON list.

    """
    path = Path(path) if path else DEFAULT_VELO_FILE
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("vELO weights JSON must be a list of route entries")
    return import_velo_weights(data)
