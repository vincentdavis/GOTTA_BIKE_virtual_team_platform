"""Zwift Racing figures on the event pages, read from the rider profile cache.

The rider tables on the event pages take their Zwift Racing figures from
``rider_data.RiderProfile`` (zauth's merged profile), not the zwiftracing tables: the
``zr_*`` keys the templates have always read (:func:`zr_fields`), and the extra columns
the signups table offers (:data:`STAT_GROUPS`). Those extra columns travel as compact JSON
(:func:`stat_rows`) that the page turns into cells, rather than as cells hidden in every
row: there are 53 of them, and a big event would carry hundreds of kilobytes of markup
nobody had asked to see.

vELO 2's mixed category is shown beside each Zwift Racing category (vELO 1's mixed one).
zwiftracing also gives every rider a women's vELO 2 category; that one is kept for women
(``RiderProfile.velo2_women``) and appears only where it is asked for: the signups table's
optional columns and its filter.

The By Category tab counts each squad's members on three scales (:func:`category_tables`):
Zwift's own category, vELO 1's tier and vELO 2's, each mixed and, for a woman, women's.

Squad eligibility still reads the zwiftracing table (``views._rider_categories``): moving
the rule that decides who may join a squad is a separate decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, NamedTuple

from apps.rider_data.models import CURVE_DURATIONS, ZR_TIERS, RiderProfile, is_woman

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def profiles_by_zwid(zwids: Iterable[int | None]) -> dict[int, RiderProfile]:
    """Load the cached profiles of some riders in one query.

    Args:
        zwids: Zwift IDs; blanks are skipped.

    Returns:
        ``{zwid: RiderProfile}`` for every one with a cached profile.

    """
    wanted = {zwid for zwid in zwids if zwid}
    return RiderProfile.objects.in_bulk(wanted) if wanted else {}


def _whole(value: float | None) -> int | None:
    return round(value) if value is not None else None


def zr_fields(profile: RiderProfile | None, gender: str | None = None) -> dict:
    """Return the ``zr_*`` keys the event templates read, from a rider's profile.

    Ratings are whole numbers, as zwiftracing shows them. ``in_zwiftracing`` says whether
    zauth holds a Zwift Racing record for the rider. ``zr_v2_category`` is vELO 2's mixed
    category, shown beside ``zr_category`` (vELO 1's mixed one); ``zr_v2_women_category`` is a
    woman's women's category, blank for anyone else, which only the signups table's filter reads.

    Args:
        profile: The rider's cached profile, or None.
        gender: The member's own gender (``User.gender``); blank leaves it to the profile.

    Returns:
        The keys, blank without a profile.

    """
    if profile is None:
        return {
            "in_zwiftracing": False,
            "zr_category": "",
            "zr_v2_category": "",
            "zr_v2_women_category": "",
            "zr_rating": None,
            "zr_age": "",
            "zr_current_rating": None,
            "zr_current_category": "",
            "zr_max30_rating": None,
            "zr_max30_category": "",
            "zr_max90_rating": None,
            "zr_max90_category": "",
            "zr_phenotype": "",
        }
    peaks = profile.peak_ratings or {}
    categories = profile.peak_categories or {}
    women = profile.velo2_women(gender) or {}
    rating = _whole(profile.velo)
    return {
        "in_zwiftracing": bool((profile.has_account or {}).get("zwiftracing")),
        "zr_category": profile.category_racing,
        "zr_v2_category": (profile.velo2 or {}).get("category") or "",
        "zr_v2_women_category": women.get("category") or "",
        "zr_rating": rating,
        "zr_age": profile.age,
        "zr_current_rating": rating,
        "zr_current_category": profile.category_racing,
        "zr_max30_rating": _whole(peaks.get("max30")),
        "zr_max30_category": categories.get("max30", ""),
        "zr_max90_rating": _whole(peaks.get("max90")),
        "zr_max90_category": categories.get("max90", ""),
        "zr_phenotype": profile.phenotype_value,
    }


# The keys of zr_fields that the rider popover (accounts/_user_tooltip.html) is given.
TOOLTIP_FIELDS = ("in_zwiftracing", "zr_category", "zr_v2_category", "zr_rating", "zr_age", "zr_phenotype")


def velo2_filter_options(entries: Iterable[dict]) -> dict[str, list[str]]:
    """Return the vELO 2 tiers the signups table can be filtered to: the ones its riders hold.

    Args:
        entries: Enriched signups, each carrying :func:`zr_fields`' keys.

    Returns:
        ``mixed`` and ``women`` tier lists, best tier first; a name zwiftracing adds later
        goes at the end rather than being dropped.

    """
    entries = list(entries)
    options = {}
    for name, key in (("mixed", "zr_v2_category"), ("women", "zr_v2_women_category")):
        held = {entry.get(key) for entry in entries} - {"", None}
        options[name] = [tier for tier in ZR_TIERS if tier in held] + sorted(held - set(ZR_TIERS))
    return options


# --- the extra columns on the signups table --------------------------------------------------


@dataclass(frozen=True)
class StatColumn:
    """One extra column: its key (the cells' ``data-col``), its names, and how it is read."""

    key: str
    label: str  # in the column menu, under its group's title
    header: str  # the table header, which has no group title beside it
    decimals: int
    read: Callable[[_Blocks], float | str | None]
    kind: str = "num"  # "num", or "category" for a category name shown as a badge


@dataclass(frozen=True)
class StatGroup:
    """A titled group of extra columns in the column menu."""

    title: str
    columns: tuple[StatColumn, ...]


class _Blocks:
    """A profile's blocks, each read once however many columns use it."""

    def __init__(self, profile: RiderProfile, gender: str | None = None) -> None:
        self.velo2 = profile.velo2 or {}
        self.velo2_women = profile.velo2_women(gender) or {}
        self.seed = profile.seed_ratings or {}
        self.phenotype = profile.phenotype_scores or {}
        self.handicaps = profile.handicaps or {}
        self.power = profile.power_extras or {}
        self.compound = profile.compound_score
        self.watts = profile.power_curve_w or {}
        self.wkg = profile.power_curve_wkg or {}
        self.races = profile.race_counts or {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _from(block: str, key: str) -> Callable[[_Blocks], float | None]:
    return lambda blocks: _number(getattr(blocks, block).get(key))


def _name_from(block: str, key: str) -> Callable[[_Blocks], str | None]:
    def read(blocks: _Blocks) -> str | None:
        value = getattr(blocks, block).get(key)
        return value if isinstance(value, str) and value else None

    return read


def _w_prime_kj(blocks: _Blocks) -> float | None:
    joules = _number(blocks.power.get("awc"))
    return joules / 1000 if joules is not None else None


# The six vELO 2 / seed factors: key, name in the menu, name in a header.
_FACTORS = (
    ("sprint", "Sprint", "Sprint"),
    ("punch", "Punch", "Punch"),
    ("climb", "Climb", "Climb"),
    ("time_trial", "Time trial", "TT"),
    ("endurance", "Endurance", "Endurance"),
    ("pursuit", "Pursuit", "Pursuit"),
)
_HANDICAPS = (("flat", "Flat"), ("rolling", "Rolling"), ("hilly", "Hilly"), ("mountainous", "Mountainous"))

STAT_GROUPS: tuple[StatGroup, ...] = (
    StatGroup(
        "vELO 2",
        (
            StatColumn("zr_v2_race", "Race rating", "vELO 2", 0, _from("velo2", "race")),
            StatColumn("zr_v2_cat", "Category", "vELO 2 Cat", 0, _name_from("velo2", "category"), "category"),
            # The women's categories are blank for anyone but a woman: see RiderProfile.velo2_women.
            StatColumn(
                "zr_v2_cat_w",
                "Women's category",
                "vELO 2 Cat (W)",
                0,
                _name_from("velo2_women", "category"),
                "category",
            ),
            StatColumn("zr_v2_max30", "30-day peak", "vELO 2 Max30", 0, _from("velo2", "max30")),
            StatColumn(
                "zr_v2_max30_cat",
                "30-day peak category",
                "vELO 2 Max30 Cat",
                0,
                _name_from("velo2", "category_max30"),
                "category",
            ),
            StatColumn(
                "zr_v2_max30_cat_w",
                "30-day peak women's category",
                "vELO 2 Max30 Cat (W)",
                0,
                _name_from("velo2_women", "max30"),
                "category",
            ),
            StatColumn("zr_v2_max90", "90-day peak", "vELO 2 Max90", 0, _from("velo2", "max90")),
            StatColumn(
                "zr_v2_max90_cat",
                "90-day peak category",
                "vELO 2 Max90 Cat",
                0,
                _name_from("velo2", "category_max90"),
                "category",
            ),
            StatColumn(
                "zr_v2_max90_cat_w",
                "90-day peak women's category",
                "vELO 2 Max90 Cat (W)",
                0,
                _name_from("velo2_women", "max90"),
                "category",
            ),
            *(
                StatColumn(f"zr_v2_{key}", label, f"v2 {short}", 0, _from("velo2", key))
                for key, label, short in _FACTORS
            ),
        ),
    ),
    StatGroup(
        "Seed",
        (
            StatColumn("zr_seed_race", "Race", "Seed Race", 0, _from("seed", "race")),
            StatColumn("zr_seed_tt", "Time trial", "Seed TT", 0, _from("seed", "time_trial")),
            # The seed has a time-trial rating as well as a time-trial factor.
            *(
                StatColumn(f"zr_seed_{key}", label, f"Seed {short}", 0, _from("seed", key))
                for key, label, short in _FACTORS
                if key != "time_trial"
            ),
            StatColumn(
                "zr_seed_tt_factor", "Time trial factor", "Seed TT factor", 0, _from("seed", "time_trial_factor")
            ),
        ),
    ),
    StatGroup(
        "Phenotype scores",
        tuple(
            StatColumn(f"zr_ph_{key}", label, label, 1, _from("phenotype", key))
            for key, label in (
                ("sprinter", "Sprinter"),
                ("puncheur", "Puncheur"),
                ("pursuiter", "Pursuiter"),
                ("climber", "Climber"),
                ("tt", "Time trialist"),
            )
        ),
    ),
    StatGroup(
        "Terrain handicaps",
        tuple(
            StatColumn(f"zr_hc_{key}", label, f"{label} handicap", 1, _from("handicaps", key))
            for key, label in _HANDICAPS
        ),
    ),
    StatGroup(
        "Power",
        (
            StatColumn("zr_compound", "Compound score", "Compound", 0, lambda blocks: _number(blocks.compound)),
            StatColumn("zr_cp", "CP (W)", "CP", 0, _from("power", "cp")),
            StatColumn("zr_wprime", "W′ (kJ)", "W′ kJ", 1, _w_prime_kj),
            *(
                StatColumn(f"zr_wkg_{seconds}", f"{name} W/kg", f"{name} W/kg", 2, _from("wkg", seconds))
                for seconds, name in CURVE_DURATIONS
            ),
            *(
                StatColumn(f"zr_w_{seconds}", f"{name} watts", f"{name} W", 0, _from("watts", seconds))
                for seconds, name in CURVE_DURATIONS
            ),
        ),
    ),
    StatGroup(
        "Races",
        tuple(
            StatColumn(f"zr_{key}", label, label, 0, _from("races", key))
            for key, label in (("finishes", "Finishes"), ("wins", "Wins"), ("podiums", "Podiums"), ("dnfs", "DNFs"))
        ),
    ),
)
STAT_COLUMNS: tuple[StatColumn, ...] = tuple(column for group in STAT_GROUPS for column in group.columns)


def stat_columns_json() -> list[dict[str, str]]:
    """Return the extra columns as the page's script needs them.

    Returns:
        ``{key, header, kind}`` per column, in :data:`STAT_COLUMNS` order.

    """
    return [{"key": column.key, "header": column.header, "kind": column.kind} for column in STAT_COLUMNS]


def _display(value: float, decimals: int) -> str:
    """Format a figure as the templates' ``floatformat`` does, halves rounding up.

    Python's own formatting rounds a half to even, so 440.5 would read 440 in the table and
    441 on the rider's profile card.

    Returns:
        The figure to ``decimals`` places.

    """
    return str(Decimal(repr(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))


def stat_values(profile: RiderProfile, gender: str | None = None) -> list[str]:
    """Return a rider's extra columns as the cells show them, in :data:`STAT_COLUMNS` order.

    Args:
        profile: The rider's cached profile.
        gender: The member's own gender, which decides the women's categories.

    Returns:
        One string per column; empty where the profile has no value.

    """
    blocks = _Blocks(profile, gender)
    values = []
    for column in STAT_COLUMNS:
        value = column.read(blocks)
        if value is None:
            values.append("")
        elif column.kind == "category":
            values.append(value)
        else:
            values.append(_display(value, column.decimals))
    return values


def stat_rows(entries: Iterable[dict]) -> dict[str, list[str]]:
    """Return the extra columns of the signups table, by signup, for the page to build cells from.

    Args:
        entries: Enriched signups (``views._enrich_signups``), each with its ``rider_profile``.

    Returns:
        ``{signup pk: values}`` for every signup whose rider has a profile.

    """
    return {
        str(entry["signup"].pk): stat_values(entry["rider_profile"], entry.get("gender"))
        for entry in entries
        if entry.get("rider_profile") is not None
    }


# --- the By Category tab ------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryScale:
    """One scale the By Category tab counts riders on.

    Attributes:
        key: Names its table and its button in the page.
        label: What the button says.
        order: Its categories, best first; a name it does not list is added after them.
        score_label: The heading of the average beside the counts.

    """

    key: str
    label: str
    order: tuple[str, ...]
    score_label: str


CATEGORY_SCALES: tuple[CategoryScale, ...] = (
    CategoryScale("zwift", "Zwift", ("A+", "A", "B", "C", "D", "E"), "Avg Racing Score"),
    CategoryScale("velo1", "vELO 1", ZR_TIERS, "Avg rating"),
    CategoryScale("velo2", "vELO 2", ZR_TIERS, "Avg vELO 2"),
)


class CategoryCount(NamedTuple):
    """One count on the By Category tab: riders by their mixed category, women by their women's."""

    mixed: int = 0
    women: int = 0


def rider_categories(profile: RiderProfile | None, gender: str | None = None) -> dict:
    """Put one rider in a category on each scale.

    Zwift's category is zauth's (Zwift's own, then ZwiftPower's), not the ZwiftPower table's. A
    women's category is a woman's only, judged by her own gender and then zauth's, as everywhere.

    Args:
        profile: The rider's cached profile, or None.
        gender: The member's own gender; blank leaves it to the profile.

    Returns:
        ``woman``, and per scale key ``(mixed category, women's category, score)``: "" where
        there is no category, None where there is no score.

    """
    woman = is_woman(gender, profile.gender if profile else None)
    if profile is None:
        return {"woman": woman, **{scale.key: ("", "", None) for scale in CATEGORY_SCALES}}
    velo2 = profile.velo2 or {}
    velo1_women = (profile.velo_women(gender) or {}).get("category") or ""
    velo2_women = (profile.velo2_women(gender) or {}).get("category") or ""
    return {
        "woman": woman,
        "zwift": (profile.category_open, profile.category_women if woman else "", profile.zwift_racing_score),
        "velo1": (profile.category_racing, velo1_women, profile.velo),
        "velo2": (velo2.get("category") or "", velo2_women, velo2.get("race")),
    }


def _count(riders: list[dict], key: str, columns: list[str]) -> dict:
    """Count riders on one scale: everyone by mixed category, each woman again by her women's.

    Returns:
        ``cells`` in column order, ``none`` (no category), ``total`` (riders, women) and ``avg``.

    """
    mixed = dict.fromkeys(columns, 0)
    women = dict.fromkeys(columns, 0)
    mixed_none = women_none = woman_count = 0
    scores = []
    for rider in riders:
        category, women_category, score = rider[key]
        if category:
            mixed[category] += 1
        else:
            mixed_none += 1
        if score is not None:
            scores.append(score)
        if rider["woman"]:
            woman_count += 1
            if women_category:
                women[women_category] += 1
            else:
                women_none += 1
    return {
        "cells": [CategoryCount(mixed[name], women[name]) for name in columns],
        "none": CategoryCount(mixed_none, women_none),
        "total": CategoryCount(len(riders), woman_count),
        "avg": round(sum(scores) / len(scores)) if scores else None,
    }


def category_tables(squads: Iterable) -> list[dict]:
    """Count each squad's members by category on every scale, for the By Category tab.

    Every rider is counted once by their mixed category, or under none, and every woman once
    more by her women's category, so a cell is a :class:`CategoryCount` and a row's total is its
    riders and its women.

    Args:
        squads: Squads with ``enriched_members`` (``views._enrich_squad_members``), each member
            carrying its ``rider_profile`` and ``gender``.

    Returns:
        A table per scale, in :data:`CATEGORY_SCALES` order: ``key``, ``label``,
        ``score_label``, ``columns``, ``has_women``, ``rows`` (each ``squad`` with its counts)
        and ``totals`` (the same counts for every squad).

    """
    squads = list(squads)
    placed = {
        squad.pk: [rider_categories(m.get("rider_profile"), m.get("gender")) for m in squad.enriched_members]
        for squad in squads
    }
    everyone = [rider for squad in squads for rider in placed[squad.pk]]
    tables = []
    for scale in CATEGORY_SCALES:
        # A name the scale does not list still gets a column, so no rider drops out of the counts.
        seen = {name for rider in everyone for name in rider[scale.key][:2] if name}
        columns = [*scale.order, *sorted(seen - set(scale.order))]
        totals = _count(everyone, scale.key, columns)
        tables.append({
            "key": scale.key,
            "label": scale.label,
            "score_label": scale.score_label,
            "columns": columns,
            "rows": [{"squad": squad, **_count(placed[squad.pk], scale.key, columns)} for squad in squads],
            "totals": totals,
            "has_women": totals["total"].women > 0,
        })
    return tables
