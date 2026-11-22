"""The signup CSV: which columns can be chosen, in what order, and how each cell is read.

One list (:func:`export_groups`) drives both the download dialog on the event page and the
file itself, so the two cannot drift apart. The file's columns always come out in this
list's order, whatever order they were ticked in.

A download names its columns (``?cols=name,zwid,...``); one that names none gets the
default columns, which is what the CSV carried before columns could be chosen (plus vELO 1's
women's categories). Name is always included, so every row can be told apart.

Who may download, and whether the Email column is offered at all, is the view's business:
the Email column only exists here when the event's ``signup_export_emails`` is on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from apps.events import rider_stats
from gotta_bike_platform.csv_utils import csv_safe

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django.http import QueryDict

    from apps.events.models import Event

NAME = "name"
EMAIL = "email"


class _Row:
    """One signup as the columns read it; the costlier lookups run once, when first asked for."""

    def __init__(self, entry: dict) -> None:
        self.entry = entry
        self.user = entry["user"]
        self.signup = entry["signup"]

    @cached_property
    def stats(self) -> dict:
        profile = self.entry.get("rider_profile")
        return rider_stats.stat_cells(profile, self.entry.get("gender")) if profile is not None else {}

    @cached_property
    def velo1_women(self) -> dict:
        profile = self.entry.get("rider_profile")
        return (profile.velo_women(self.entry.get("gender")) or {}) if profile is not None else {}

    @cached_property
    def answers(self) -> dict[int, str]:
        return {answer["question"].pk: answer["display"] for answer in self.entry["custom_answers"]}


@dataclass(frozen=True)
class ExportColumn:
    """One column the download can include.

    Attributes:
        key: Names it in ``?cols=``.
        header: Its heading in the file.
        read: Reads its cell from a row.
        label: Its name in the dialog, under its group's title; the header when blank.
        default: Ticked when nothing has been chosen.
        table_cols: The signup table's columns it mirrors (``col:<data-col>`` or
            ``stat:<key>``), for the dialog's "Match table" button.

    """

    key: str
    header: str
    read: Callable[[_Row], object]
    label: str = ""
    default: bool = True
    table_cols: tuple[str, ...] = ()

    @property
    def choice_label(self) -> str:
        """Its name in the dialog."""
        return self.label or self.header

    @property
    def table_cols_attr(self) -> str:
        """:attr:`table_cols` as the dialog's ``data-table-cols`` attribute."""
        return " ".join(self.table_cols)


@dataclass(frozen=True)
class ExportGroup:
    """A titled group of columns in the dialog; ``optional`` ones start collapsed and unticked."""

    key: str
    title: str
    columns: tuple[ExportColumn, ...]
    optional: bool = False


def _or_blank(value: object) -> object:
    """Blank a missing figure, and a zero one: no rider has an FTP or a rating of 0.

    Returns:
        The value, or "".

    """
    return value or ""


def _blank_if_none(value: object) -> object:
    """Blank a missing figure only: a real 0 (no wins yet) stays 0.

    Returns:
        The value, or "" when it is None.

    """
    return "" if value is None else value


def _zauth(row: _Row) -> str:
    user = row.user
    if user.is_zauth_verified:
        return "Zauth"
    if user.zwid_verified:
        return user.get_zwid_verification_method_display() or "Other"
    return ""


def _race_verified(row: _Row) -> str:
    if row.entry["is_extra_verified"]:
        return "Extra Verified"
    return "Yes" if row.entry["is_race_ready"] else "No"


def _entry(key: str, *, blank: Callable[[object], object] = _or_blank) -> Callable[[_Row], object]:
    return lambda row: blank(row.entry[key])


def _velo1_women(key: str) -> Callable[[_Row], str]:
    return lambda row: row.velo1_women.get(key) or ""


def _stat(key: str) -> Callable[[_Row], object]:
    return lambda row: _blank_if_none(row.stats.get(key))


def _answer(question_pk: int) -> Callable[[_Row], str]:
    return lambda row: row.answers.get(question_pk, "")


_ZWIFTPOWER = ExportGroup(
    "zwiftpower",
    "ZwiftPower",
    (
        ExportColumn(
            "zp_category", "ZP Category", _entry("zp_category"), label="Category", table_cols=("col:category",)
        ),
        ExportColumn(
            "zp_category_w",
            "ZP Category (W)",
            _entry("zp_category_w"),
            label="Women's category",
            table_cols=("col:category",),
        ),
        ExportColumn("ftp", "FTP", _entry("zp_ftp"), table_cols=("col:ftp",)),
        ExportColumn("wkg", "WKG", _entry("wkg"), label="W/kg", table_cols=("col:wkg",)),
    ),
)

_ZWIFT = ExportGroup(
    "zwift",
    "Zwift",
    (
        ExportColumn("zftp", "zFTP", _entry("z_ftp"), table_cols=("col:zftp",)),
        ExportColumn("zftp_wkg", "zFTP W/kg", _entry("z_ftp_wkg"), table_cols=("col:zftp",)),
        ExportColumn("zmap", "zMAP", _entry("z_map"), table_cols=("col:zmap",)),
        ExportColumn("zmap_wkg", "zMAP W/kg", _entry("z_map_wkg"), table_cols=("col:zmap_wkg",)),
    ),
)

_VERIFICATION = ExportGroup(
    "verification",
    "Verification",
    (
        ExportColumn("zauth", "Zauth", _zauth, table_cols=("col:zauth",)),
        ExportColumn("race_verified", "Race Verified", _race_verified, table_cols=("col:verified",)),
    ),
)

# vELO 1: zwiftracing's original rating. Headed "vELO 1" so a file holding both scales can't
# confuse it with vELO 2, which runs on another scale. The women's categories are blank for
# anyone but a woman, as RiderProfile.velo_women decides.
_VELO1 = ExportGroup(
    "velo1",
    "vELO 1",
    (
        ExportColumn("velo1", "vELO 1", _entry("zr_current_rating"), label="Rating", table_cols=("col:zr_cur_rating",)),
        ExportColumn(
            "velo1_cat",
            "vELO 1 Cat",
            _entry("zr_current_category"),
            label="Category",
            table_cols=("col:rating", "col:zr_cur_cat"),
        ),
        ExportColumn(
            "velo1_cat_w",
            "vELO 1 Cat (W)",
            _velo1_women("category"),
            label="Women's category",
            table_cols=("col:rating", "col:zr_cur_cat"),
        ),
        ExportColumn(
            "velo1_max30",
            "vELO 1 Max30",
            _entry("zr_max30_rating"),
            label="30-day peak",
            table_cols=("col:zr_max30_rating",),
        ),
        ExportColumn(
            "velo1_max30_cat",
            "vELO 1 Max30 Cat",
            _entry("zr_max30_category"),
            label="30-day peak category",
            table_cols=("col:zr_max30_cat",),
        ),
        ExportColumn(
            "velo1_max30_cat_w",
            "vELO 1 Max30 Cat (W)",
            _velo1_women("max30"),
            label="30-day peak women's category",
            table_cols=("col:zr_max30_cat",),
        ),
        ExportColumn(
            "velo1_max90",
            "vELO 1 Max90",
            _entry("zr_max90_rating"),
            label="90-day peak",
            table_cols=("col:zr_max90_rating",),
        ),
        ExportColumn(
            "velo1_max90_cat",
            "vELO 1 Max90 Cat",
            _entry("zr_max90_category"),
            label="90-day peak category",
            table_cols=("col:zr_max90_cat",),
        ),
        ExportColumn(
            "velo1_max90_cat_w",
            "vELO 1 Max90 Cat (W)",
            _velo1_women("max90"),
            label="90-day peak women's category",
            table_cols=("col:zr_max90_cat",),
        ),
    ),
)

_ZWIFT_RACING = ExportGroup(
    "zwift-racing",
    "Zwift Racing",
    (
        ExportColumn("zr_age", "Age", _entry("zr_age"), table_cols=("col:zr_age",)),
        ExportColumn("zr_phenotype", "Phenotype", _entry("zr_phenotype"), table_cols=("col:zr_phenotype",)),
    ),
)

# The signup table's optional Zwift Racing columns (vELO 2, seed, power and so on): offered
# here too, unticked and collapsed, under the same keys and headers as the table's.
_STAT_GROUPS = tuple(
    ExportGroup(
        "stats-" + re.sub(r"[^a-z0-9]+", "-", group.title.lower()).strip("-"),
        group.title,
        tuple(
            ExportColumn(
                column.key,
                column.header,
                _stat(column.key),
                label=column.label,
                default=False,
                table_cols=(f"stat:{column.key}",),
            )
            for column in group.columns
        ),
        optional=True,
    )
    for group in rider_stats.STAT_GROUPS
)


def export_groups(event: Event, questions: Iterable) -> tuple[ExportGroup, ...]:
    """Return every column this event's download can include, grouped, in file order.

    Args:
        event: The event; decides the Email, Squad Gender and Event Role columns.
        questions: Its active signup questions, one column each.

    Returns:
        The groups, the Rider group first and the optional stat groups last.

    """
    rider = [
        ExportColumn(
            NAME,
            "Name",
            lambda row: row.user.get_full_name() or row.user.discord_username or "",
            table_cols=("col:name",),
        ),
        ExportColumn("discord", "Discord", lambda row: row.user.discord_username or ""),
    ]
    if event.signup_export_emails:
        rider.append(ExportColumn(EMAIL, "Email", lambda row: (row.user.email or "").strip(), default=False))
    rider += [
        ExportColumn("zwid", "ZWID", _entry("zwid"), table_cols=("col:zwid",)),
        ExportColumn(
            "timezone",
            "Timezone",
            lambda row: ", ".join(row.signup.signup_timezone or []),
            table_cols=("col:timezone",),
        ),
    ]
    if event.squad_gender_required:
        rider.append(
            ExportColumn(
                "squad_gender",
                "Squad Gender",
                lambda row: ", ".join(row.signup.signup_squad_gender or []),
                table_cols=("col:squad_gender",),
            )
        )
    rider.append(ExportColumn("gender", "Gender", _entry("gender"), table_cols=("col:gender",)))
    if event.event_role:
        rider.append(
            ExportColumn(
                "event_role",
                "Event Role",
                lambda row: "Yes" if row.entry["has_event_role"] else "No",
                table_cols=("col:event_role",),
            )
        )
    rider += [
        ExportColumn(
            "squads",
            "Squads",
            lambda row: ", ".join(squad.name for squad in row.entry["assigned_squads"]),
            table_cols=("col:squad",),
        ),
        ExportColumn("notes", "Notes", lambda row: row.signup.notes or "", table_cols=("col:notes",)),
        ExportColumn(
            "signed_up",
            "Signed Up",
            lambda row: row.signup.created_at.isoformat() if row.signup.created_at else "",
        ),
    ]

    groups = [ExportGroup("rider", "Rider", tuple(rider)), _ZWIFTPOWER, _ZWIFT, _VERIFICATION, _VELO1, _ZWIFT_RACING]
    question_columns = tuple(
        ExportColumn(f"q{question.pk}", question.label, _answer(question.pk), table_cols=("col:custom_answers",))
        for question in questions
    )
    if question_columns:
        groups.append(ExportGroup("questions", "Signup questions", question_columns))
    return (*groups, *_STAT_GROUPS)


def requested_keys(params: QueryDict) -> list[str] | None:
    """Read the column keys a download asked for.

    ``cols`` may repeat (the dialog's checkboxes) or hold a comma-separated list (a link).

    Args:
        params: The request's query string.

    Returns:
        The keys, in the order given; None when the download named no columns at all.

    """
    if "cols" not in params:
        return None
    return [key.strip() for raw in params.getlist("cols") for key in raw.split(",") if key.strip()]


def chosen_columns(
    groups: Iterable[ExportGroup], requested: Iterable[str] | None = None, *, also: Iterable[str] = ()
) -> list[ExportColumn]:
    """Pick the columns a download gets, in file order.

    Keys the event doesn't offer are ignored, and Name is always included.

    Args:
        groups: From :func:`export_groups`.
        requested: The keys asked for; None for the default columns.
        also: Keys to add on top (the old ``?email=1`` link).

    Returns:
        The columns, in :func:`export_groups` order.

    """
    columns = [column for group in groups for column in group.columns]
    wanted = {column.key for column in columns if column.default} if requested is None else set(requested)
    wanted |= {NAME, *also}
    return [column for column in columns if column.key in wanted]


def build_rows(columns: Iterable[ExportColumn], entries: Iterable[dict]) -> tuple[list, list[list]]:
    """Build the file's header and rows.

    Every cell goes through ``csv_safe``: notes, answers and Discord names are rider-authored,
    and a cell starting with = + - or @ would run as a formula when the file is opened.

    Args:
        columns: From :func:`chosen_columns`.
        entries: Enriched signups (``views._enrich_signups``).

    Returns:
        ``(header, rows)`` ready to hand to ``csv.writer``.

    """
    columns = list(columns)
    header = [csv_safe(column.header) for column in columns]
    rows = []
    for entry in entries:
        row = _Row(entry)
        rows.append([csv_safe(column.read(row)) for column in columns])
    return header, rows
