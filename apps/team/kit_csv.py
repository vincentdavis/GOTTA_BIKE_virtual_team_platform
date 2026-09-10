"""CSV export and import of team kit statuses, for /site/config/team_kit/.

The export is the member list as the page shows it (same filters), with one ``kit:<slug>``
column per kit. The import reads that same shape back: edit the kit columns in a spreadsheet,
upload, check the preview, confirm. A blank cell means "leave it alone", so a sheet that fills
in one kit -- or only some riders -- changes exactly what it fills in and nothing else.

Rows are matched on ``user_id``, the platform's own id. Neither Zwift ID nor Discord id is
unique on a user, and a Discord id is too long for a spreadsheet to keep intact (Excel keeps 15
significant digits and quietly rounds the rest). ``zwid`` is accepted when there is no
``user_id`` -- for a sheet built by hand from a Zwift list -- and refused where two members
share it. When a row carries both, they must agree, which catches the classic spreadsheet
accident of sorting one column without the others.

An export is a snapshot, and riders keep changing their own status after it is taken. So each
exported row also records what it said (``exported_statuses``), and the import merges three
ways rather than two: a cell still matching its export was not edited and is left alone --
whatever the rider has set since stands -- and a cell that was edited, for a status that has
ALSO moved since the export, is a conflict that is shown and not applied. Without that column
(a sheet built by hand) every filled cell simply sets the status, and the preview is the check.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from django.db import transaction

from apps.team.kits import BADGE_CLASSES, RIDER_LABELS, status_for, team_members
from apps.team.models import KitStatus, TeamKit
from gotta_bike_platform.csv_utils import csv_safe

if TYPE_CHECKING:
    from datetime import date

    from apps.accounts.models import User

KIT_COLUMN_PREFIX = "kit:"

# The export's descriptive columns. Read back, they are ignored -- they are there so a person
# can tell the rows apart -- except user_id and zwid, which pick the member.
INFO_COLUMNS: tuple[str, ...] = (
    "user_id",
    "discord_name",
    "discord_username",
    "zwift_name",
    "zwid",
    "zwift_verified",
)

# What each row's statuses were when exported, as "slug=status" pairs. The last column, so it
# is out of the way of the ones being edited.
SNAPSHOT_COLUMN = "exported_statuses"

MAX_IMPORT_BYTES = 1_048_576

# How the import tells one preview from the next. See ``team_kit_import_confirm``.
SESSION_KEY = "team_kit_import"


def kit_column(kit: TeamKit) -> str:
    """Name the CSV column holding one kit's statuses.

    Keyed by slug, not name: the slug is what every status is stored under and cannot change,
    while a kit's name can be edited between an export and its import. The prefix keeps a kit
    column from ever being mistaken for one of the descriptive columns.

    Args:
        kit: The kit.

    Returns:
        The column header.

    """
    return f"{KIT_COLUMN_PREFIX}{kit.slug}"


def export_filename(*, verified_only: bool, needs_kit_only: bool, today: date) -> str:
    """Name the export file after the day and the filters it was taken with.

    Args:
        verified_only: Whether the verified filter was on.
        needs_kit_only: Whether the needs-the-kit filter was on.
        today: The date to stamp it with.

    Returns:
        The filename.

    """
    parts = ["team-kit", today.isoformat()]
    if verified_only:
        parts.append("verified")
    if needs_kit_only:
        parts.append("need")
    return "-".join(parts) + ".csv"


def export_table(rows: list[dict], kits: list[TeamKit]) -> tuple[list[str], list[list]]:
    """Build the export's header and rows.

    Every kit gets a column, retired ones included: the export is the team's whole record,
    and a retired kit's statuses are kept rather than dropped (see ``apply_kit_fields``).
    Statuses are written as their stored keys ("need", "submitted") -- short to type when
    editing, and never renamed the way a label might be.

    Args:
        rows: Member rows from ``kit_member_rows``.
        kits: Every kit, in display order.

    Returns:
        ``(header, data_rows)``.

    """
    header = [*INFO_COLUMNS, *(kit_column(kit) for kit in kits), SNAPSHOT_COLUMN]
    data = []
    for row in rows:
        member = row["user"]
        statuses = [status_for(member, kit) for kit in kits]
        data.append([
            member.pk,
            # Discord and Zwift names are chosen by the rider, so they go through the formula
            # guard. The other cells are ids, yes/no and fixed status keys.
            csv_safe(row["discord_name"] or ""),
            csv_safe(row["discord_username"] or ""),
            csv_safe(row["zwift_name"] or ""),
            member.zwid or "",
            "yes" if row["zwid_verified"] else "no",
            *statuses,
            # Guarded too: an admin-typed slug may begin with "-".
            csv_safe(" ".join(f"{kit.slug}={status}" for kit, status in zip(kits, statuses, strict=True))),
        ])
    return header, data


def _normalise(text: str) -> str:
    """Fold a status cell for matching: case, spacing and curly apostrophes.

    Args:
        text: The raw text.

    Returns:
        The folded text.

    """
    return " ".join(text.replace("\u2019", "'").casefold().split())


# Every spelling accepted for each status: the stored key, the team's label and the rider's
# label -- so a cell can say "need", "Need kit" or "I need the kit" and mean the same thing.
STATUS_SPELLINGS: dict[str, str] = {
    **{_normalise(status.value): status.value for status in KitStatus},
    **{_normalise(status.label): status.value for status in KitStatus},
    **{_normalise(label): str(status) for status, label in RIDER_LABELS.items()},
}


def parse_status(value: str) -> str | None:
    """Read one kit cell.

    Args:
        value: The cell, as written.

    Returns:
        The ``KitStatus`` value, or None for a blank cell (leave it alone).

    Raises:
        ValueError: If the cell holds something that is not a status.

    """
    folded = _normalise(value)
    if not folded:
        return None
    try:
        return STATUS_SPELLINGS[folded]
    except KeyError:
        raise ValueError(value) from None


def _member_name(member: User) -> str:
    """Name a member the way the kit page does.

    Args:
        member: The member.

    Returns:
        Their Discord nickname, else username, else their id.

    """
    return member.discord_nickname or member.discord_username or f"User #{member.pk}"


@dataclass
class ImportChange:
    """One status the import would change."""

    member: User
    kit: TeamKit
    old_raw: object  # exactly what was stored, for the "changed since the preview" check
    old: str
    new: str

    @property
    def member_name(self) -> str:
        """Name the member as the kit page does.

        Returns:
            The display name.

        """
        return _member_name(self.member)

    @property
    def old_label(self) -> str:
        """Word the current status for the preview.

        Returns:
            The team's label for it.

        """
        return KitStatus(self.old).label

    @property
    def new_label(self) -> str:
        """Word the new status for the preview.

        Returns:
            The team's label for it.

        """
        return KitStatus(self.new).label

    @property
    def old_badge(self) -> str:
        """Colour the current status as the kit page does.

        Returns:
            A badge class.

        """
        return BADGE_CLASSES.get(self.old, "badge-ghost")

    @property
    def new_badge(self) -> str:
        """Colour the new status as the kit page does.

        Returns:
            A badge class.

        """
        return BADGE_CLASSES.get(self.new, "badge-ghost")

    def for_session(self) -> dict:
        """Reduce to what confirming needs, in a JSON-safe form.

        Returns:
            ``{"user_id", "kit", "old", "new"}``.

        """
        return {"user_id": self.member.pk, "kit": self.kit.slug, "old": self.old_raw, "new": self.new}


@dataclass
class ImportConflict:
    """An edited cell for a status that has also moved since the export. Never applied."""

    member: User
    kit: TeamKit
    exported: str  # what the export said
    now: str  # what is stored now
    wanted: str  # what the file asks for

    @property
    def member_name(self) -> str:
        """Name the member as the kit page does.

        Returns:
            The display name.

        """
        return _member_name(self.member)

    @property
    def labels(self) -> dict[str, str]:
        """Word the three statuses for the preview.

        Returns:
            ``{"exported", "now", "wanted"}`` labels.

        """
        return {name: KitStatus(getattr(self, name)).label for name in ("exported", "now", "wanted")}


@dataclass
class ImportPlan:
    """What an uploaded file would do. Built without writing anything."""

    fatal: str = ""  # a problem with the whole file; nothing else is filled in
    changes: list[ImportChange] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)  # {"row": n, "message": str}
    refused_rows: set[int] = field(default_factory=set)  # every row number not used
    ignored_columns: list[str] = field(default_factory=list)
    unknown_kit_columns: list[str] = field(default_factory=list)
    kits: list[TeamKit] = field(default_factory=list)  # the kit columns that were recognised
    unchanged: int = 0  # statuses the file sets to what they already are
    kept_newer: int = 0  # unedited cells whose status has moved since the export; left as they are

    @property
    def member_count(self) -> int:
        """Count the members the changes touch.

        Returns:
            The number of distinct members.

        """
        return len({change.member.pk for change in self.changes})

    @property
    def refused_row_count(self) -> int:
        """Count the rows not used. Not ``len(errors)``: one duplicate error covers several rows.

        Returns:
            The number of rows.

        """
        return len(self.refused_rows)


class _Columns(NamedTuple):
    """Where the columns the import reads are."""

    user_id: int | None
    zwid: int | None
    snapshot: int | None
    kits: list[tuple[int, TeamKit]]


# The columns read to pick the member and do the merge; everything else but kit columns is ignored.
_READ_COLUMNS = ("user_id", "zwid", SNAPSHOT_COLUMN)


def _read_header(header: list[str], kits: list[TeamKit], plan: ImportPlan) -> _Columns:
    """Work out which column is which.

    Only a column the import actually reads can be ambiguous. A repeated "notes" column is
    ignored twice, and two kits whose slugs differ only in case are two different columns.

    Args:
        header: The first row of the file.
        kits: Every kit.
        plan: The plan to record problems on.

    Returns:
        The column positions. Sets ``plan.fatal`` if the header cannot be used.

    """
    by_slug = {kit.slug: kit for kit in kits}
    by_folded: dict[str, list[TeamKit]] = defaultdict(list)
    for kit in kits:
        by_folded[kit.slug.casefold()].append(kit)

    found: dict[str, int] = {}
    kit_columns: list[tuple[int, TeamKit]] = []
    empty = _Columns(None, None, None, [])
    for index, raw in enumerate(header):
        name = raw.strip()
        folded = name.casefold()
        if not name:
            continue  # a trailing comma, or a spacer column

        if folded in _READ_COLUMNS:
            if folded in found:
                plan.fatal = f'The column "{name}" appears twice, so it is not clear which to use.'
                return empty
            found[folded] = index
        elif folded.startswith(KIT_COLUMN_PREFIX):
            slug = name[len(KIT_COLUMN_PREFIX) :].strip()
            # Exact first; a case-insensitive match only when it points at one kit.
            candidates = by_folded.get(slug.casefold(), [])
            kit = by_slug.get(slug) or (candidates[0] if len(candidates) == 1 else None)
            if kit is None:
                if name not in plan.unknown_kit_columns:
                    plan.unknown_kit_columns.append(name)
            elif any(existing is kit for _, existing in kit_columns):
                plan.fatal = f'Two columns are for "{kit.name}", so it is not clear which to use.'
                return empty
            else:
                kit_columns.append((index, kit))
        elif folded not in INFO_COLUMNS and name not in plan.ignored_columns:
            plan.ignored_columns.append(name)

    if "user_id" not in found and "zwid" not in found:
        plan.fatal = 'Add a "user_id" or "zwid" column so each row can be matched to a member. An export has both.'
    elif not kit_columns:
        expected = ", ".join(f'"{kit_column(kit)}"' for kit in kits) or "none yet"
        plan.fatal = f"No kit columns found. Kit columns are headed kit:<slug> -- for this team: {expected}."
    return _Columns(found.get("user_id"), found.get("zwid"), found.get(SNAPSHOT_COLUMN), kit_columns)


def _parse_id(raw: str) -> int | None:
    """Read an id cell as a plain run of ASCII digits.

    ``int()`` alone is looser than a spreadsheet id should be: it takes "1_2" as 12, "+12",
    and full-width digits.

    Args:
        raw: The cell.

    Returns:
        The number, or None if the cell is not one.

    """
    return int(raw) if raw.isascii() and raw.isdigit() else None


def _read_snapshot(raw: str) -> tuple[dict[str, str] | None, str]:
    """Read a row's ``exported_statuses`` cell.

    Args:
        raw: The cell.

    Returns:
        ``({slug: status}, "")``; ``(None, "")`` for a blank cell (no merge for this row); or
        ``(None, reason)`` when it has been damaged. Entries for kits that no longer exist are
        harmless and simply never looked up.

    """
    # The formula guard may have prefixed an apostrophe on export.
    text = raw.removeprefix("'").strip()
    if not text:
        return None, ""
    snapshot = {}
    for pair in text.split():
        slug, sep, status = pair.partition("=")
        if not sep or not slug or status not in KitStatus.values:
            return None, (
                f"{SNAPSHOT_COLUMN} has been changed and cannot be read. Clear that cell to import this row "
                "without the check, or export again."
            )
        snapshot[slug] = status
    return snapshot, ""


def _match_member(
    raw_id: str, raw_zwid: str, by_id: dict[int, User], by_zwid: dict[int, list[User]]
) -> tuple[User | None, str]:
    """Find the member a row is about.

    Args:
        raw_id: The row's user_id cell.
        raw_zwid: The row's zwid cell.
        by_id: Team members by id.
        by_zwid: Team members by Zwift ID.

    Returns:
        ``(member, "")``; ``(None, reason)`` when the row cannot be matched; or
        ``(member, reason)`` when user_id names a member but the row's zwid is not theirs.

    """
    zwid = None
    if raw_zwid:
        zwid = _parse_id(raw_zwid)
        if zwid is None:
            return None, f'zwid "{raw_zwid}" is not a number.'

    if raw_id:
        user_id = _parse_id(raw_id)
        if user_id is None:
            return None, f'user_id "{raw_id}" is not a number.'
        member = by_id.get(user_id)
        if member is None:
            return None, f"No team member has user_id {raw_id}."
        if zwid is not None and member.zwid != zwid:
            # The member is still returned, beside the problem: the row is refused either way,
            # but it is still one of this member's rows when counting duplicates.
            return member, (
                f"user_id {raw_id} is {_member_name(member)}, whose Zwift ID is {member.zwid or 'not set'}, "
                f"not {zwid}. Was one column sorted without the others?"
            )
        return member, ""

    if zwid is None:
        return None, "No user_id or zwid, so this row cannot be matched to a member."
    matches = by_zwid.get(zwid, [])
    if not matches:
        return None, f"No team member has Zwift ID {zwid}."
    if len(matches) > 1:
        return None, f"{len(matches)} team members have Zwift ID {zwid}. Add a user_id column to say which."
    return matches[0], ""


def _delimiter(text: str) -> str:
    """Pick the file's delimiter from its header line.

    Excel in any locale that writes decimals with a comma (most of Europe) saves "CSV" with
    semicolons, and a sheet pasted from elsewhere may be tab-separated. Read as commas, either
    arrives as one long column and fails with a misleading "add a user_id column".

    Args:
        text: The decoded file.

    Returns:
        Whichever of comma, semicolon and tab the header line uses most; comma on a tie.

    """
    header_line = text.split("\n", 1)[0]
    return max((",", ";", "\t"), key=header_line.count)


def _merge_cell(plan: ImportPlan, member: User, kit: TeamKit, wanted: str, *, exported: str | None) -> None:
    """Decide what one filled-in cell does, and record it on the plan.

    Args:
        plan: The plan to record on.
        member: The row's member.
        kit: The column's kit.
        wanted: The status the cell holds.
        exported: What the row's export said for this kit, or None when the row has no
            snapshot for it (a sheet built by hand, or a kit added since the export).

    """
    now = status_for(member, kit)
    if now == wanted:
        plan.unchanged += 1
        return
    if exported is not None and wanted == exported:
        # Not edited -- the cell is still what the export said, and the status has moved on
        # since (the rider, or another admin). The newer status stands.
        plan.kept_newer += 1
        return
    if exported is not None and now != exported:
        # Edited, but the status has moved since the export too. Neither side can be assumed
        # to be the one that should win, so it is shown and left alone.
        plan.conflicts.append(ImportConflict(member=member, kit=kit, exported=exported, now=now, wanted=wanted))
        return
    plan.changes.append(
        ImportChange(member=member, kit=kit, old_raw=(member.team_kit or {}).get(kit.slug), old=now, new=wanted)
    )


def build_import_plan(raw: bytes, kits: list[TeamKit]) -> ImportPlan:
    """Read an uploaded file and work out what it would change, without changing anything.

    A row with any problem is left out whole -- never half-applied -- and listed with its
    row number, so the preview shows exactly what will and will not happen. A member on more
    than one row is refused on all of them rather than letting whichever came last win. Each
    filled-in cell is then merged against the row's export snapshot -- see ``_merge_cell``.

    Args:
        raw: The uploaded file's bytes.
        kits: Every kit.

    Returns:
        The plan.

    """
    plan = ImportPlan()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        plan.fatal = (
            'The file is not UTF-8 text. In Excel, save it as "CSV UTF-8"; a Google Sheets CSV download already is.'
        )
        return plan

    # Strict, so an unclosed quote in a hand-edited file is refused rather than quietly
    # swallowing every row after it into one cell. Spreadsheet output is unaffected.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(text), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            plan.fatal = "The file is empty."
            return plan
        columns = _read_header(header, kits, plan)
        if plan.fatal:
            return plan
        plan.kits = [kit for _, kit in columns.kits]

        members = list(team_members().only("id", "discord_username", "discord_nickname", "zwid", "team_kit"))
        by_id = {member.pk: member for member in members}
        by_zwid: dict[int, list[User]] = defaultdict(list)
        for member in members:
            if member.zwid:
                by_zwid[member.zwid].append(member)

        rows_by_member: dict[int, list[int]] = defaultdict(list)
        accepted: list[tuple[User, list[tuple[TeamKit, str]], dict[str, str] | None]] = []
        for row_number, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue

            def cell(index: int | None, cells: list[str] = cells) -> str:
                return cells[index].strip() if index is not None and index < len(cells) else ""

            member, member_problem = _match_member(cell(columns.user_id), cell(columns.zwid), by_id, by_zwid)
            # Counted however the rest of the row turns out: a member on two rows is refused on
            # both even when one of them has its own problem, or the other would still apply.
            if member is not None:
                rows_by_member[member.pk].append(row_number)

            statuses: list[tuple[TeamKit, str]] = []
            unreadable: list[str] = []
            for index, kit in columns.kits:
                try:
                    status = parse_status(cell(index))
                except ValueError:
                    unreadable.append(f'"{cell(index)}" under {kit_column(kit)}')
                    continue
                if status is not None:
                    statuses.append((kit, status))
            status_problem = (
                f"Not a kit status: {', '.join(unreadable)}. Use one of: {', '.join(KitStatus.values)}."
                if unreadable
                else ""
            )
            snapshot, snapshot_problem = _read_snapshot(cell(columns.snapshot))

            problems = [problem for problem in (member_problem, status_problem, snapshot_problem) if problem]
            if problems:
                plan.errors.append({"row": row_number, "message": " ".join(problems)})
                plan.refused_rows.add(row_number)
                continue
            if statuses:  # otherwise every kit cell is blank: nothing asked for
                accepted.append((member, statuses, snapshot))
    except csv.Error as exc:
        plan.fatal = f"The file could not be read as CSV ({exc})."
        return plan

    duplicated = {pk: rows for pk, rows in rows_by_member.items() if len(rows) > 1}
    for pk, rows in duplicated.items():
        plan.errors.append({
            "row": rows[0],
            "message": (
                f"{_member_name(by_id[pk])} is on rows {', '.join(map(str, rows))}. "
                "Keep one row per member -- none of these rows were used."
            ),
        })
        plan.refused_rows.update(rows)
    plan.errors.sort(key=lambda error: error["row"])

    for member, statuses, snapshot in accepted:
        if member.pk in duplicated:
            continue
        for kit, wanted in statuses:
            _merge_cell(plan, member, kit, wanted, exported=(snapshot or {}).get(kit.slug))
    column_order = {kit.pk: position for position, kit in enumerate(plan.kits)}

    def display_order(item: ImportChange | ImportConflict) -> tuple:
        return item.member_name.casefold(), item.member.pk, column_order[item.kit.pk]

    plan.changes.sort(key=display_order)
    plan.conflicts.sort(key=display_order)
    return plan


def apply_import(changes: list[dict]) -> tuple[list[dict], int]:
    """Write a confirmed import.

    Each change applies only if the stored value is still what the preview saw. Anything that
    moved in between -- the rider changed it themselves, another admin imported over it, the
    kit or the member has gone -- is skipped and counted, so confirming a preview can never
    overwrite something the person confirming did not see.

    Args:
        changes: The preview's changes, as ``ImportChange.for_session`` left them.

    Returns:
        ``(applied_changes, skipped_count)``.

    """
    from apps.accounts.models import User

    by_member: dict[int, list[dict]] = defaultdict(list)
    for change in changes:
        by_member[change["user_id"]].append(change)
    existing_slugs = set(TeamKit.objects.values_list("slug", flat=True))

    applied: list[dict] = []
    with transaction.atomic():
        members = team_members().select_for_update().filter(pk__in=list(by_member)).only("id", "team_kit")
        to_save = []
        for member in members:
            team_kit = dict(member.team_kit or {})
            touched = False
            for change in by_member[member.pk]:
                if change["kit"] not in existing_slugs or team_kit.get(change["kit"]) != change["old"]:
                    continue
                team_kit[change["kit"]] = change["new"]
                applied.append(change)
                touched = True
            if touched:
                member.team_kit = team_kit
                to_save.append(member)
        User.objects.bulk_update(to_save, ["team_kit"])
    return applied, len(changes) - len(applied)
