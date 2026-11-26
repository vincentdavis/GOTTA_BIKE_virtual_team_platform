"""Sorting the riders on a stage-series sheet's results page by their figures.

The options are the signup download's columns (:mod:`apps.events.signup_export`), so a
figure is named and read the same way on the signups table, in the CSV and here.

Which figures a viewer may sort by follows the signups table. Every team member can sort by
what the rider popover already shows them (ZwiftPower category, vELO 1 rating and category,
vELO 2 category); power, the other ratings and race counts only those who see the full
signups table. A sort gives away a ranking even without its numbers, so a figure the viewer
may not see is left out of the page altogether, not just out of the menu.

The page sorts in the browser: :func:`sort_data` gives it the menu and each rider's values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.events import rider_stats, signup_export
from apps.rider_data.models import ZR_TIERS, member_gender
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPTeamRiders

if TYPE_CHECKING:
    from collections.abc import Iterable

    from apps.accounts.models import User
    from apps.events.models import Event

#: The figures any team member may sort by: what the rider popover already shows everyone.
PUBLIC_KEYS = frozenset({"zp_category", "zp_category_w", "velo1", "velo1_cat", "zr_v2_cat"})

# signup_export's groups that hold figures; the others are text (names, notes, verification).
_FIGURE_GROUPS = frozenset({"zwiftpower", "zwift", "velo1"})

_ZP_ORDER = tuple(category for _, category in sorted(ZP_DIV_TO_CATEGORY.items()))
_ZP_KEYS = frozenset({"zp_category", "zp_category_w"})
# Every column whose values are category names: ZwiftPower's, vELO 1's and the table's own.
_CATEGORY_KEYS = frozenset({
    *_ZP_KEYS,
    "velo1_cat",
    "velo1_cat_w",
    "velo1_max30_cat",
    "velo1_max30_cat_w",
    "velo1_max90_cat",
    "velo1_max90_cat_w",
    *(column.key for column in rider_stats.STAT_COLUMNS if column.kind == "category"),
})


@dataclass(frozen=True)
class SortOption:
    """One figure the riders can be sorted by.

    Attributes:
        key: The signup download's column key.
        header: Its name in the menu and over the figure shown beside each rider.
        category: A category, ranked best first, rather than a number.

    """

    key: str
    header: str
    category: bool = False


@dataclass(frozen=True)
class SortGroup:
    """A titled group of options in the menu."""

    title: str
    options: tuple[SortOption, ...]


def _entries(users: Iterable[User]) -> list[dict]:
    """Gather the figures the download's columns read, for riders who are not signups.

    Returns:
        One dict per rider, with the keys ``signup_export``'s figure columns read.

    """
    users = list(users)
    zwids = [user.zwid for user in users if user.zwid]
    zp_by_zwid = {rider.zwid: rider for rider in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    profiles = rider_stats.profiles_by_zwid(zwids)
    entries = []
    for user in users:
        zp = zp_by_zwid.get(user.zwid)
        profile = profiles.get(user.zwid) if user.zwid else None
        ftp = zp.ftp if zp else None
        weight = zp.weight if zp else None
        entries.append({
            "user": user,
            "signup": None,
            "custom_answers": [],
            "rider_profile": profile,
            "gender": member_gender(user, getattr(profile, "gender", "")),
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            "zp_ftp": ftp,
            "wkg": round(Decimal(ftp) / weight, 2) if ftp and weight and weight > 0 else None,
            "z_ftp": user.z_ftp,
            "z_ftp_wkg": user.z_ftp_wkg,
            "z_map": user.z_map,
            "z_map_wkg": user.z_map_wkg,
            **rider_stats.zr_fields(profile, gender=user.gender),
        })
    return entries


def _rank(key: str, value: object) -> float | None:
    """Turn a figure into a number to sort on, larger being better.

    Categories rank by their order, best first, rather than by name; one the order doesn't
    know goes below the ones it does.

    Returns:
        The number, or None when the rider has no value.

    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        order = _ZP_ORDER if key in _ZP_KEYS else ZR_TIERS
        return -float(order.index(value)) if value in order else -float(len(order))
    return float(value)


def _shown(value: object) -> str:
    """Write a figure as it is shown beside the rider: whole numbers grouped, others to two places.

    Returns:
        The text.

    """
    if isinstance(value, str):
        return value
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def sort_data(event: Event, users: Iterable[User], *, full: bool) -> tuple[list[SortGroup], dict[str, dict]]:
    """Build the sort menu and each rider's values for it.

    Args:
        event: The event, which the download's column list is built for.
        users: The riders on the page.
        full: Whether the viewer sees the full signups table, and so every figure.

    Returns:
        ``(groups, values)``: the menu's groups, in the download's order, and
        ``{user pk: {key: [rank, shown]}}``, a key left out where the rider has no value.

    """
    columns: list[signup_export.ExportColumn] = []
    groups = []
    for group in signup_export.export_groups(event, []):
        if group.key not in _FIGURE_GROUPS and not group.optional:
            continue
        offered = [column for column in group.columns if full or column.key in PUBLIC_KEYS]
        if not offered:
            continue
        groups.append(
            SortGroup(
                group.title,
                tuple(SortOption(column.key, column.header, column.key in _CATEGORY_KEYS) for column in offered),
            )
        )
        columns += offered

    values: dict[str, dict] = {}
    for entry in _entries(users):
        row = signup_export.ExportRow(entry)
        figures = {}
        for column in columns:
            value = column.read(row)
            rank = _rank(column.key, value)
            if rank is not None:
                figures[column.key] = [rank, _shown(value)]
        values[str(entry["user"].pk)] = figures
    return groups, values
