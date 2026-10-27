"""Race-series availability: an event's stages, each squad's stage times, riders' answers.

A race-series event (``Event.availability_type`` = series) lists its stages
(``SeriesStage``). Each squad has one series sheet -- an ``AvailabilityGrid`` of kind
series -- holding a ``SheetStage`` per stage with the time the squad races it, and each
rider answers every stage Available, Maybe or No in their response's ``stage_answers``.

An answer remembers the start time it was given for. When a captain moves a stage, the
answers given for the old time stop counting and those riders are asked again. A weekly
grid freezes its shape once anyone has answered; a series cannot, because moving a race is
routine in a league, and only the moved stage's answers are in doubt.

A racing stage's lineup is an ordinary scheduled race (``AvailabilitySlotSelection``) at
the stage's UTC date and time, so riders, substitutes, DSs, Discord threads and the
calendar file all work as they do for a weekly sheet. Moving a stage moves its race.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.events.models import AvailabilityGrid, AvailabilityResponse, AvailabilitySlotSelection

if TYPE_CHECKING:
    from collections.abc import Iterable

    from apps.events.models import Event, SeriesStage, SheetStage, Squad

NOTE_MAX_LENGTH = 200
#: A series sheet still fills the grid's required slot length. Nothing reads it.
SERIES_SLOT_MINUTES = 60

ANSWER = AvailabilityResponse.StageAnswer
ANSWER_VALUES = frozenset(ANSWER.values)

#: Answered, but for a start time the stage has since moved from.
STALE = "stale"
#: Not answered at all.
UNANSWERED = "none"


def stamp(starts_at: datetime) -> str:
    """Name a stage's start time the way an answer records it.

    Minute precision in UTC, so one moment always reads the same however it was entered.

    Args:
        starts_at: An aware datetime.

    Returns:
        A string like "2026-11-03T19:30Z".

    """
    return starts_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def slot_key(starts_at: datetime) -> tuple[date, str]:
    """Where a stage's scheduled race sits: its UTC date and "HH:MM" time.

    Args:
        starts_at: An aware datetime.

    Returns:
        ``(utc_date, "HH:MM")``, the key ``AvailabilitySlotSelection`` is unique on per grid.

    """
    utc = starts_at.astimezone(UTC)
    return utc.date(), utc.strftime("%H:%M")


def zone(name: str | None) -> ZoneInfo:
    """Look a timezone up by name, falling back to UTC.

    Args:
        name: An IANA timezone name, or blank.

    Returns:
        The zone, or UTC when the name is blank or unknown.

    """
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except KeyError, ValueError:
        return ZoneInfo("UTC")


def when_label(starts_at: datetime, tz: ZoneInfo) -> str:
    """Write a start time for a rider: "Tue 3 Nov · 20:30".

    Args:
        starts_at: An aware datetime.
        tz: The zone to show it in.

    Returns:
        The label.

    """
    local = starts_at.astimezone(tz)
    return f"{local:%a} {local.day} {local:%b} · {local:%H:%M}"


def route_summary(stage: SeriesStage) -> str:
    """Describe a stage's course: "Innsbruckring · 3 laps · 26.4 km".

    The distance is the route's laps without the lead-in, which is what riders compare.

    Args:
        stage: The stage, with its route loaded.

    Returns:
        The summary, or "" when the stage has no route.

    """
    if not stage.route:
        return ""
    parts = [stage.route.name]
    if stage.laps > 1:
        parts.append(f"{stage.laps} laps")
    distance = stage.route.distance_km * stage.laps
    if distance:
        parts.append(f"{distance:.1f} km")
    return " · ".join(parts)


def window_label(stage: SeriesStage) -> str:
    """Write a stage's window for a captain, such as "10-16 Nov" (with an en dash).

    Args:
        stage: The stage.

    Returns:
        The label, or "" when the stage has no window.

    """
    start, end = stage.window_start, stage.window_end
    if start and end:
        if (start.year, start.month) == (end.year, end.month):
            return f"{start.day}\u2013{end.day} {end:%b}"
        return f"{start.day} {start:%b} \u2013 {end.day} {end:%b}"
    if start:
        return f"from {start.day} {start:%b}"
    if end:
        return f"until {end.day} {end:%b}"
    return ""


def window_weekday(stage: SeriesStage, weekday: int) -> date | None:
    """Find the first day in a stage's window falling on a weekday.

    Lets a captain fill "every stage on Tuesdays" in one go.

    Args:
        stage: The stage.
        weekday: Monday 0 to Sunday 6.

    Returns:
        The date, or None when the stage has no window start or the window has no such day.

    """
    if not stage.window_start:
        return None
    day = stage.window_start + timedelta(days=(weekday - stage.window_start.weekday()) % 7)
    if stage.window_end and day > stage.window_end:
        return None
    return day


def event_stages(event: Event) -> list[SeriesStage]:
    """List an event's stages in series order, routes loaded.

    Args:
        event: The race-series event.

    Returns:
        The stages, first stage first.

    """
    return list(event.series_stages.select_related("route"))


def series_sheet(squad: Squad) -> AvailabilityGrid | None:
    """Find a squad's series sheet. A squad has at most one.

    Args:
        squad: The squad.

    Returns:
        The sheet, or None if the captain has not set the squad's stage times yet.

    """
    return squad.availability_grids.filter(kind=AvailabilityGrid.Kind.SERIES).order_by("created_at").first()


def answer_state(answers: dict | None, sheet_stage: SheetStage) -> str:
    """Read one rider's answer to one stage.

    Args:
        answers: The rider's ``stage_answers``, or None for no response.
        sheet_stage: The squad's time for the stage.

    Returns:
        An ``ANSWER`` value; ``STALE`` when the answer was given for a time the stage no
        longer has; ``UNANSWERED`` otherwise, including for a stage riders cannot answer.

    """
    if not sheet_stage.is_open:
        return UNANSWERED
    entry = (answers or {}).get(str(sheet_stage.stage_id))
    if not entry or entry.get("answer") not in ANSWER_VALUES:
        return UNANSWERED
    if entry.get("for") != stamp(sheet_stage.starts_at):
        return STALE
    return entry["answer"]


def needs_answer(sheet_stages: Iterable[SheetStage], answers: dict | None, *, after: datetime | None = None) -> bool:
    """Whether a rider still owes a sheet an answer.

    Args:
        sheet_stages: The sheet's stages.
        answers: The rider's ``stage_answers``, or None for no response.
        after: When given, stages starting at or before it are ignored: asking about a
            race that has already started helps nobody.

    Returns:
        True while any open stage is unanswered or was answered for an old time.

    """
    return any(
        s.is_open and (after is None or s.starts_at > after) and answer_state(answers, s) in {UNANSWERED, STALE}
        for s in sheet_stages
    )


def sync_sheet_span(grid: AvailabilityGrid, sheet_stages: Iterable[SheetStage]) -> None:
    """Keep a series sheet's date/time span equal to its first and last open stage.

    The span is what the participation report, My Events and the pending badge read, so
    with it kept true they need no series branch. Not saved here.

    Args:
        grid: The series sheet.
        sheet_stages: Its stages, as they will be saved.

    """
    times = sorted(s.starts_at for s in sheet_stages if s.is_open)
    if times:
        grid.start_date, grid.start_time = slot_key(times[0])
        grid.end_date, grid.end_time = slot_key(times[-1])
    else:
        grid.start_date = grid.end_date = timezone.now().date()
        grid.start_time = grid.end_time = "00:00"
    grid.slot_duration = SERIES_SLOT_MINUTES


@dataclass
class Tally:
    """How a squad answered one stage."""

    available: int = 0
    maybe: int = 0
    no: int = 0
    stale: int = 0
    unanswered: int = 0


def tally(sheet_stage: SheetStage, answers_by_user: dict[int, dict], member_ids: Iterable[int]) -> Tally:
    """Count a squad's answers to one stage.

    Args:
        sheet_stage: The squad's time for the stage.
        answers_by_user: ``stage_answers`` by user id, current members only.
        member_ids: The squad's current members.

    Returns:
        The counts, one per member.

    """
    counts = Tally()
    for user_id in member_ids:
        state = answer_state(answers_by_user.get(user_id), sheet_stage)
        if state == STALE:
            counts.stale += 1
        elif state == UNANSWERED:
            counts.unanswered += 1
        else:
            setattr(counts, state, getattr(counts, state) + 1)
    return counts


def riders_status(stage: SeriesStage, counts: Tally) -> tuple[str, str] | None:
    """Judge whether a squad has riders enough for a stage.

    Args:
        stage: The stage, for its fewest riders.
        counts: The squad's answers.

    Returns:
        ``(key, label)``: "enough" when the Available riders cover it, "tight" when it
        needs some of the Maybes, "short" when even they do not. None when the stage sets
        no minimum.

    """
    if not stage.riders_min:
        return None
    if counts.available >= stage.riders_min:
        return "enough", "Enough riders"
    if counts.available + counts.maybe >= stage.riders_min:
        return "tight", "Tight"
    return "short", "Short"


def selections_by_key(grid: AvailabilityGrid) -> dict[tuple[date, str], AvailabilitySlotSelection]:
    """Map a sheet's scheduled races to the stage times they sit at.

    Args:
        grid: The series sheet.

    Returns:
        Races by ``slot_key``, riders and substitutes prefetched.

    """
    return {
        (sel.slot_date, sel.slot_time): sel
        for sel in grid.slot_selections.prefetch_related("selected_users", "substitutes")
    }


def move_scheduled_races(
    grid: AvailabilityGrid, moves: list[tuple[datetime, datetime]]
) -> tuple[list[AvailabilitySlotSelection], list[AvailabilitySlotSelection]]:
    """Carry each moved stage's scheduled race to the stage's new time.

    Races leaving their time are first parked on a placeholder time, so two stages that
    swap times never collide on the (grid, date, time) uniqueness the races are held to.
    A race whose new time is taken by a race that is not moving stays where it is. Run it
    inside the caller's transaction, so the placeholder is never seen.

    Args:
        grid: The series sheet.
        moves: ``(old_start, new_start)`` for every stage whose time changed.

    Returns:
        ``(moved, blocked)``: the races moved, and the ones left behind.

    """
    by_key = {(sel.slot_date, sel.slot_time): sel for sel in grid.slot_selections.all()}
    moving = [(by_key[slot_key(old)], new) for old, new in moves if slot_key(old) in by_key]
    leaving = {sel.pk for sel, _ in moving}
    held = {key for key, sel in by_key.items() if sel.pk not in leaving}
    blocked = [sel for sel, new in moving if slot_key(new) in held]
    moving = [(sel, new) for sel, new in moving if sel not in blocked]
    for index, (sel, _new) in enumerate(moving):
        sel.slot_time = f"~{index}"
        sel.save(update_fields=["slot_time", "updated_at"])
    for sel, new in moving:
        sel.slot_date, sel.slot_time = slot_key(new)
        sel.save(update_fields=["slot_date", "slot_time", "updated_at"])
    return [sel for sel, _ in moving], blocked


def answered_stage_ids(event: Event) -> set[int]:
    """Find the stages riders have answered, which the stages page will not delete.

    Args:
        event: The race-series event.

    Returns:
        Stage pks with at least one stored answer on any of the event's sheets.

    """
    ids: set[int] = set()
    for answers in AvailabilityResponse.objects.filter(
        grid__squad__event=event, grid__kind=AvailabilityGrid.Kind.SERIES
    ).values_list("stage_answers", flat=True):
        ids.update(int(key) for key in (answers or {}) if key.isdigit())
    return ids


def rider_stage_status(
    sheet_stage: SheetStage,
    answers: dict | None,
    selection: AvailabilitySlotSelection | None,
    user_pk: int,
) -> tuple[str, str]:
    """Say what one stage means for one rider, for their My Events page.

    A lineup outranks the rider's own answer: once picked, that is the news.

    Args:
        sheet_stage: The squad's time for the stage.
        answers: The rider's ``stage_answers``, or None.
        selection: The stage's scheduled race, riders and substitutes prefetched, or None.
        user_pk: The rider.

    Returns:
        ``(kind, label)``, kind being one of racing, lineup, sub, not_picked, picking,
        available, maybe, no, again, unanswered, off, untimed.

    """
    if not sheet_stage.racing:
        return "off", "Not racing"
    if not sheet_stage.starts_at:
        return "untimed", "Time not set yet"
    if selection is not None:
        confirmed = selection.status == AvailabilitySlotSelection.Status.CONFIRMED
        if any(u.pk == user_pk for u in selection.selected_users.all()):
            return ("racing", "You're racing") if confirmed else ("lineup", "In the draft lineup")
        if any(u.pk == user_pk for u in selection.substitutes.all()):
            return "sub", "Substitute"
        if confirmed:
            return "not_picked", "Not in the lineup"
        return "picking", "Lineup being picked"
    state = answer_state(answers, sheet_stage)
    return {
        ANSWER.AVAILABLE: ("available", "You said Available"),
        ANSWER.MAYBE: ("maybe", "You said Maybe"),
        ANSWER.NO: ("no", "You said No"),
        STALE: ("again", "Answer again"),
    }.get(state, ("unanswered", "Not answered"))
