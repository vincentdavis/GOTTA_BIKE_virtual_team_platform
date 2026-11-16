"""Summary statistics for a squad's riders, for the Stats section of the manage-squads cards.

Each figure (zMAP, zMAP W/kg, vELO 2) is summarised over the riders who have one: a rider
missing a figure is left out of that figure only, and the card says how many riders each
column counts. zMAP and its W/kg are the stored ones the signups table shows
(``User.z_map``, over the weight Zwift computed it with); vELO 2 is zwiftracing's race
rating from the rider data cache, a different scale from vELO 1.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def _z_map(member: dict) -> float | None:
    return _number(member.get("z_map"))


def _z_map_wkg(member: dict) -> float | None:
    return _number(member.get("z_map_wkg"))


def _velo2(member: dict) -> float | None:
    profile = member.get("rider_profile")
    return _number(((profile.velo2 if profile else None) or {}).get("race"))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    return float(value)


# label, decimal places shown, how to read the figure from an enriched member dict
FIGURES: tuple[tuple[str, int, Callable[[dict], float | None]], ...] = (
    ("zMAP", 0, _z_map),
    ("zMAP W/kg", 2, _z_map_wkg),
    ("vELO 2", 0, _velo2),
)


@dataclass(frozen=True)
class Figure:
    """One figure's summary over the riders who have it.

    Attributes:
        label: The column heading.
        places: Decimal places shown.
        values: The riders' values; empty when no rider has this figure.

    """

    label: str
    places: int
    values: tuple[float, ...]

    def summary(self) -> dict[str, float | None]:
        """Return min, max, median, mean and range, all None when no rider has the figure.

        Returns:
            The five statistics keyed by name.

        """
        if not self.values:
            return dict.fromkeys(("min", "max", "median", "mean", "range"))
        low, high = min(self.values), max(self.values)
        return {
            "min": low,
            "max": high,
            "median": statistics.median(self.values),
            "mean": statistics.fmean(self.values),
            "range": high - low,
        }

    def show(self, value: float | None) -> str:
        """Format a value at this figure's precision.

        Returns:
            The value, or an empty string for None.

        """
        return "" if value is None else f"{value:,.{self.places}f}"


ROWS = (("min", "Min"), ("max", "Max"), ("median", "Median"), ("mean", "Mean"), ("range", "Range (max-min)"))


@dataclass(frozen=True)
class SquadStats:
    """A squad's Stats table: one column per figure, one row per statistic.

    Attributes:
        figures: The figures, in column order.
        rider_count: How many riders the squad has.

    """

    figures: tuple[Figure, ...]
    rider_count: int

    @property
    def has_data(self) -> bool:
        """Whether any rider has any of the figures."""
        return any(figure.values for figure in self.figures)

    @property
    def rows(self) -> list[dict]:
        """The table body: each statistic's label and its formatted value for every figure.

        Returns:
            ``{"label", "values"}`` per statistic, in :data:`ROWS` order.

        """
        summaries = [figure.summary() for figure in self.figures]
        return [
            {
                "label": label,
                "values": [figure.show(summary[key]) for figure, summary in zip(self.figures, summaries, strict=True)],
            }
            for key, label in ROWS
        ]

    @property
    def counts(self) -> list[int]:
        """How many riders each figure counts, in column order.

        Returns:
            One count per figure.

        """
        return [len(figure.values) for figure in self.figures]


def squad_stats(members: Iterable[dict]) -> SquadStats:
    """Summarise a squad's riders.

    Args:
        members: The squad's enriched member dicts (``events.views._enrich_squad_members``).

    Returns:
        The squad's Stats table.

    """
    members = list(members)
    figures = []
    for label, places, read in FIGURES:
        values = tuple(value for value in map(read, members) if value is not None)
        figures.append(Figure(label=label, places=places, values=values))
    return SquadStats(figures=tuple(figures), rider_count=len(members))
