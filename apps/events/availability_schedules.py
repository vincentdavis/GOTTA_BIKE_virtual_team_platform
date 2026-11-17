"""Weekly availability sheets on a schedule: the engine behind ``AvailabilitySchedule``.

A squad's schedule says "every Friday at 18:00, make a sheet from this template for the week
starting seven days later, post it, remind whoever hasn't answered on Sunday and Tuesday, and
close it on Thursday". ``run_schedules`` does whatever is due; the scheduler runs it every few
minutes (``apps.events.tasks.run_availability_schedules``).

Three rules keep it predictable:

- **One sheet per post day.** The sheet records the day it was posted for, and a database
  constraint refuses a second, so overlapping runs cannot post twice.
- **Late, but not stale.** A post or reminder missed while the app was down still goes out
  within ``CATCH_UP`` of its time; after that it is dropped, not sent days late.
- **Nothing before it was asked for.** Saving or resuming a schedule after this week's time has
  passed waits for next week (``not_before``) rather than posting at once.

It never touches a sheet made by hand, except to stay out of its way: a week that already has
a sheet covering the same dates is skipped, and the schedule page says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

import logfire
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.events.models import AvailabilityGrid, AvailabilitySchedule, SquadMember
from apps.events.series import zone
from apps.events.tz_utils import convert_local_to_utc

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.events.models import AvailabilityGridTemplate, Squad

#: How late a missed post or reminder may still go out. A deploy takes minutes; an outage
#: that runs past this means the moment has gone, and a "new sheet" ping half a day late does
#: more harm than skipping it.
CATCH_UP = timedelta(hours=12)

#: Most riders one reminder mentions. Discord allows 100 user mentions and 2,000 characters a
#: message; a mention is about 22. No squad comes near it, but a message Discord refuses would
#: remind nobody at all.
MAX_MENTIONS = 60


def template_span_utc(
    template: AvailabilityGridTemplate, start_date: date, local_start_time: str, local_tz: str
) -> tuple[date, date, str, str]:
    """Work out the UTC dates and times of a sheet made from a template.

    The end date comes from the template's length. A start time other than the template's
    keeps the template's span, so a re-timed template is not silently shortened.

    Args:
        template: The template.
        start_date: The sheet's first local day.
        local_start_time: Local start time, "HH:MM".
        local_tz: The timezone the times are in.

    Returns:
        ``(start_date, end_date, start_time, end_time)`` in UTC, as the grid stores them.

    """
    end_date = start_date + timedelta(days=template.default_length_days - 1)
    span_minutes = (
        datetime.combine(date.min, time.fromisoformat(template.end_time))
        - datetime.combine(date.min, time.fromisoformat(template.start_time))
    ).total_seconds() / 60
    if span_minutes <= 0:
        span_minutes += 24 * 60
    local_end_time = (
        datetime.combine(date.min, time.fromisoformat(local_start_time)) + timedelta(minutes=span_minutes)
    ).strftime("%H:%M")

    start_utc, end_utc, start_time_utc, end_time_utc = convert_local_to_utc(
        start_date, end_date, local_start_time, local_end_time, local_tz
    )
    if template.single_slot:
        # Re-derive rather than trusting the conversion. A local time near midnight can land
        # start and end on different UTC dates, and the grid generator would then emit one
        # slot per date -- two cells from a template that means one. Same derivation the
        # builder's save uses, so both routes agree.
        end_utc = start_utc
        end_time_utc = (
            datetime.combine(date.min, time.fromisoformat(start_time_utc)) + timedelta(minutes=template.slot_duration)
        ).strftime("%H:%M")
    return start_utc, end_utc, start_time_utc, end_time_utc


def grid_from_template(
    template: AvailabilityGridTemplate,
    squad: Squad,
    start_date: date,
    *,
    local_start_time: str | None = None,
    local_tz: str | None = None,
    created_by: User | None = None,
    **fields,
) -> AvailabilityGrid:
    """Create a draft sheet for a squad from a template.

    Args:
        template: The template.
        squad: The squad the sheet is for.
        start_date: The sheet's first local day.
        local_start_time: Local start time; the template's when None.
        local_tz: The times' timezone; the template's when None.
        created_by: Who made it; None for the schedule.
        **fields: Further grid fields (``schedule``, ``scheduled_for``, ``status``).

    Returns:
        The new sheet.

    """
    local_start_time = local_start_time or template.start_time
    local_tz = local_tz or template.grid_timezone
    start_utc, end_utc, start_time_utc, end_time_utc = template_span_utc(
        template, start_date, local_start_time, local_tz
    )
    fields.setdefault("status", AvailabilityGrid.Status.DRAFT)
    # A shared template names a specific session ("Round 3 Qualifier"), and several squads
    # create grids from it -- carrying that name through beats the auto-generated
    # "<event> <squad> <dates>" title. A squad's own templates keep the auto title.
    return AvailabilityGrid.objects.create(
        squad=squad,
        title=template.name if template.shared else "",
        start_date=start_utc,
        end_date=end_utc,
        start_time=start_time_utc,
        end_time=end_time_utc,
        slot_duration=template.slot_duration,
        grid_timezone=local_tz,
        blocked_cells=[],
        max_races_question=template.max_races_question,
        rest_days_question=template.rest_days_question,
        hide_empty_days=template.hide_empty_days,
        single_slot=template.single_slot,
        expanded_features=template.expanded_features,
        description=template.description,
        website_url=template.website_url,
        course_url=template.course_url,
        recon_url=template.recon_url,
        invite_url=template.invite_url,
        expires=None,
        created_by=created_by,
        **fields,
    )


# -- When things happen ----------------------------------------------------------------------


def post_at(schedule: AvailabilitySchedule, day: date) -> datetime:
    """When the schedule posts, or reminds or closes, on a given local day.

    Args:
        schedule: The schedule.
        day: The local day.

    Returns:
        That day at the schedule's post time, in UTC.

    """
    local = datetime.combine(day, time.fromisoformat(schedule.post_time), tzinfo=zone(schedule.schedule_timezone))
    return local.astimezone(UTC)


def latest_post_day(schedule: AvailabilitySchedule, now: datetime) -> date:
    """Find the most recent post day whose post time has been reached.

    Args:
        schedule: The schedule.
        now: The current time.

    Returns:
        A local date on the schedule's weekday.

    """
    today = now.astimezone(zone(schedule.schedule_timezone)).date()
    day = today - timedelta(days=(today.weekday() - schedule.weekday) % 7)
    if post_at(schedule, day) > now:
        day -= timedelta(days=7)
    return day


def sheet_days(schedule: AvailabilitySchedule, day: date) -> tuple[date, date] | None:
    """Work out the local first and last day of the sheet posted on ``day``.

    Args:
        schedule: The schedule.
        day: The post day.

    Returns:
        ``(first, last)``, or None while the schedule has no template.

    """
    if schedule.template is None:
        return None
    first = day + timedelta(days=schedule.starts_after_days)
    return first, first + timedelta(days=schedule.template.default_length_days - 1)


@dataclass(frozen=True)
class Upcoming:
    """One coming post, for the schedule page's preview."""

    post_at: datetime
    first_day: date | None
    last_day: date | None
    reminders: tuple[datetime, ...]
    closes_at: datetime | None
    skipped: bool


def upcoming(schedule: AvailabilitySchedule, now: datetime, count: int = 3) -> list[Upcoming]:
    """List the next posts the schedule will make, with their reminders and closing time.

    Args:
        schedule: The schedule.
        now: The current time.
        count: How many to list.

    Returns:
        Up to ``count`` coming posts, earliest first; a skipped one is listed and marked.

    """
    after = max(now, schedule.not_before)
    day = latest_post_day(schedule, after) + timedelta(days=7)
    made = set(schedule.grids.values_list("scheduled_for", flat=True)) if schedule.pk else set()
    result = []
    while len(result) < count:
        if day not in made:
            span = sheet_days(schedule, day)
            result.append(
                Upcoming(
                    post_at=post_at(schedule, day),
                    first_day=span[0] if span else None,
                    last_day=span[1] if span else None,
                    reminders=tuple(post_at(schedule, day + timedelta(days=d)) for d in sorted(schedule.reminder_days)),
                    closes_at=(
                        post_at(schedule, day + timedelta(days=schedule.close_after_days))
                        if schedule.close_after_days
                        else None
                    ),
                    skipped=day == schedule.skip_date,
                )
            )
        day += timedelta(days=7)
    return result


def next_post(schedule: AvailabilitySchedule, now: datetime) -> datetime | None:
    """When the schedule next posts a sheet, skipping a skipped week.

    Args:
        schedule: The schedule.
        now: The current time.

    Returns:
        The time, or None while it is paused or has no template.

    """
    if not schedule.active or schedule.template_id is None:
        return None
    return next((u.post_at for u in upcoming(schedule, now) if not u.skipped), None)


@dataclass(frozen=True)
class Status:
    """A schedule's state in a few words, for the squad cards and the availability page."""

    state: str  # "active", "paused" or "needs_template"
    text: str


def status(schedule: AvailabilitySchedule | None, now: datetime) -> Status | None:
    """Say where a squad's schedule stands: "Next Fri 21 Nov 18:00 GMT", "Paused", ...

    Args:
        schedule: The schedule, or None.
        now: The current time.

    Returns:
        The status, or None when the squad has no schedule.

    """
    if schedule is None:
        return None
    if not schedule.active:
        return Status("paused", "Paused")
    when = next_post(schedule, now)
    if when is None:
        return Status("needs_template", "Needs a template")
    local = when.astimezone(zone(schedule.schedule_timezone))
    return Status("active", f"Next {local:%a %-d %b %H:%M %Z}")


# -- Doing it --------------------------------------------------------------------------------


def _note(schedule: AvailabilitySchedule, text: str, now: datetime) -> None:
    """Record what the job did, for the schedule page. Logged too.

    Written with ``update()`` so the schedule's ``updated_at`` keeps meaning "last saved by a
    person".

    Args:
        schedule: The schedule.
        text: One sentence.
        now: The current time.

    """
    AvailabilitySchedule.objects.filter(pk=schedule.pk).update(last_note=text[:500], last_note_at=now)
    schedule.last_note, schedule.last_note_at = text[:500], now
    logfire.info("Availability schedule", schedule_id=schedule.pk, squad_id=schedule.squad_id, note=text)


def _note_once(schedule: AvailabilitySchedule, text: str, now: datetime) -> None:
    """Record why a post could not be made, unless that is already the note on record.

    A post the job cannot make is re-examined every run until ``CATCH_UP`` passes; this keeps
    the note (and the log) to one line rather than one every few minutes.

    Args:
        schedule: The schedule.
        text: One sentence.
        now: The current time.

    """
    if schedule.last_note != text[:500]:
        _note(schedule, text, now)


def _sheet_label(grid: AvailabilityGrid) -> str:
    return f'"{grid.title}"'


def non_responders(grid: AvailabilityGrid) -> list[User]:
    """Find the squad's riders who have not answered the sheet and can be mentioned.

    Current members only (the same rule as the sheet's own response count), with a Discord
    account, still in the Discord server.

    Args:
        grid: The sheet.

    Returns:
        The riders, by name.

    """
    from apps.accounts.membership import departed_discord_ids
    from apps.accounts.models import User

    answered = grid.active_responses().values_list("user_id", flat=True)
    riders = list(
        User.objects
        .filter(squad_memberships__squad=grid.squad, squad_memberships__status=SquadMember.Status.MEMBER)
        .exclude(pk__in=answered)
        .exclude(discord_id="")
        .order_by("first_name", "last_name")
    )
    gone = departed_discord_ids(rider.discord_id for rider in riders)
    return [rider for rider in riders if rider.discord_id not in gone]


def _dm_captains_about_draft(schedule: AvailabilitySchedule, grid: AvailabilityGrid) -> int:
    """DM the squad's captains and vice-captains that a draft sheet is waiting for them.

    Args:
        schedule: The schedule.
        grid: The draft.

    Returns:
        How many were sent.

    """
    from apps.accounts.discord_service import send_discord_dm

    squad = schedule.squad
    path = reverse("events:squad_availability", kwargs={"event_pk": squad.event_id, "squad_pk": squad.pk})
    message = (
        f"A draft availability sheet for **{squad.name}** ({squad.event.title}) is ready: {grid.title}.\n"
        f"Check it and publish it here: {schedule.base_url.rstrip('/')}{path}"
    )
    leaders = {user.pk: user for user in [*squad.captains.all(), *squad.vice_captains.all()] if user.discord_id}
    return sum(1 for user in leaders.values() if send_discord_dm(user.discord_id, message))


def _post_due_sheet(schedule: AvailabilitySchedule, now: datetime) -> None:
    """Make, and publish and post, this week's sheet if it is due and not yet made."""
    from apps.events.views import _post_grid_published_notification

    day = latest_post_day(schedule, now)
    due = post_at(schedule, day)
    if due < schedule.not_before or now - due > CATCH_UP:
        return
    if schedule.grids.filter(scheduled_for=day).exists():
        return

    squad, template = schedule.squad, schedule.template
    if day == schedule.skip_date:
        _note_once(schedule, f"Skipped the {day:%a %-d %b} sheet, as asked.", now)
        return
    if template is None:
        _note_once(schedule, "No sheet was made: the schedule's template has been deleted. Pick another.", now)
        return
    if squad.event.is_race_series:
        _note_once(schedule, "No sheet was made: this event collects availability per stage.", now)
        return
    first, _last = sheet_days(schedule, day)
    if first > squad.event.end_date:
        _note_once(schedule, f"No sheet was made: the event ends on {squad.event.end_date:%a %-d %b}.", now)
        return

    start_utc, end_utc, _, _ = template_span_utc(template, first, template.start_time, template.grid_timezone)
    clash = (
        squad.availability_grids
        .filter(kind=AvailabilityGrid.Kind.WEEKLY, start_date__lte=end_utc, end_date__gte=start_utc)
        .exclude(schedule=schedule)
        .first()
    )
    if clash:
        _note_once(
            schedule,
            f"Skipped the {day:%a %-d %b} sheet: the squad already has {_sheet_label(clash)} for those dates.",
            now,
        )
        return

    publish = schedule.publish_mode == AvailabilitySchedule.PublishMode.PUBLISH
    try:
        with transaction.atomic():
            grid = grid_from_template(
                template,
                squad,
                first,
                schedule=schedule,
                scheduled_for=day,
                status=AvailabilityGrid.Status.PUBLISHED if publish else AvailabilityGrid.Status.DRAFT,
            )
    except IntegrityError:
        return  # another run made it a moment ago

    if not publish:
        sent = _dm_captains_about_draft(schedule, grid)
        _note(schedule, f"Made the draft {_sheet_label(grid)} and DMed {sent} captain{'s' * (sent != 1)}.", now)
        return
    result = _post_grid_published_notification(None, squad.event, squad, grid, base_url=schedule.base_url)
    if result is True:
        _note(schedule, f"Published {_sheet_label(grid)} and posted it to the squad's channel.", now)
    else:
        _note(schedule, f"Published {_sheet_label(grid)}, but couldn't post it to Discord. {result}", now)


def _close_if_due(schedule: AvailabilitySchedule, grid: AvailabilityGrid, now: datetime) -> bool:
    """Close a scheduled sheet once its closing time has come.

    Returns:
        True when the sheet is (now) past its closing time.

    """
    if schedule.close_after_days is None:
        return False
    if now < post_at(schedule, grid.scheduled_for + timedelta(days=schedule.close_after_days)):
        return False
    closed = AvailabilityGrid.objects.filter(pk=grid.pk, status=AvailabilityGrid.Status.PUBLISHED).update(
        status=AvailabilityGrid.Status.CLOSED, updated_at=now
    )
    if closed:
        _note(schedule, f"Closed {_sheet_label(grid)}.", now)
    return True


def _remind_if_due(schedule: AvailabilitySchedule, grid: AvailabilityGrid, now: datetime) -> None:
    """Remind the riders who haven't answered, when a reminder day has come.

    Each reminder day is claimed under a row lock before anything is sent, so two runs cannot
    both send it. Reminders missed by more than ``CATCH_UP`` are marked and dropped; when
    several are due at once only the latest is sent. A failed post hands its claim back, so
    the next run tries again.
    """
    from apps.events.views import _post_grid_published_notification

    due = [
        offset
        for offset in sorted(set(schedule.reminder_days))
        if offset not in grid.reminders_sent and post_at(schedule, grid.scheduled_for + timedelta(days=offset)) <= now
    ]
    if not due:
        return
    with transaction.atomic():
        locked = AvailabilityGrid.objects.select_for_update().get(pk=grid.pk)
        before = list(locked.reminders_sent)
        due = [offset for offset in due if offset not in before]
        if not due:
            return
        AvailabilityGrid.objects.filter(pk=grid.pk).update(reminders_sent=sorted({*before, *due}))

    if now - post_at(schedule, grid.scheduled_for + timedelta(days=due[-1])) > CATCH_UP:
        return  # missed by too long: marked, not sent
    riders = non_responders(grid)
    if not riders:
        _note(schedule, f"No reminder needed for {_sheet_label(grid)}: everyone has answered.", now)
        return
    mentioned = riders[:MAX_MENTIONS]
    result = _post_grid_published_notification(
        None,
        schedule.squad.event,
        schedule.squad,
        grid,
        reminder=True,
        base_url=schedule.base_url,
        mention_user_ids=[rider.discord_id for rider in mentioned],
    )
    if result is True:
        _note(
            schedule,
            f"Reminded {len(mentioned)} rider{'s' * (len(mentioned) != 1)} who hadn't answered {_sheet_label(grid)}.",
            now,
        )
    else:
        AvailabilityGrid.objects.filter(pk=grid.pk).update(reminders_sent=before)
        _note(schedule, f"Couldn't post the reminder for {_sheet_label(grid)}. {result}", now)


def run_schedule(schedule: AvailabilitySchedule, now: datetime | None = None) -> None:
    """Do whatever one schedule has due: close, remind, then post.

    Closing goes first so a sheet past its closing time is never reminded about.

    Args:
        schedule: The schedule.
        now: The current time (for tests); now when None.

    """
    now = now or timezone.now()
    if not schedule.active:
        return
    for grid in schedule.grids.filter(status=AvailabilityGrid.Status.PUBLISHED, scheduled_for__isnull=False):
        if not _close_if_due(schedule, grid, now):
            _remind_if_due(schedule, grid, now)
    _post_due_sheet(schedule, now)


def run_schedules(now: datetime | None = None) -> dict:
    """Run every active schedule. One schedule failing never stops the others.

    Args:
        now: The current time (for tests); now when None.

    Returns:
        ``{"schedules": n, "failed": n}``.

    """
    now = now or timezone.now()
    schedules = AvailabilitySchedule.objects.filter(active=True).select_related("squad__event", "template")
    ran = failed = 0
    for schedule in schedules:
        ran += 1
        try:
            run_schedule(schedule, now)
        except Exception:
            failed += 1
            logfire.exception("Availability schedule failed", schedule_id=schedule.pk, squad_id=schedule.squad_id)
            _note(schedule, "Something went wrong running this schedule. The details are in Logfire.", now)
    return {"schedules": ran, "failed": failed}
