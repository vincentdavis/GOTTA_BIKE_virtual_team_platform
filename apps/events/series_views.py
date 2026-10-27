"""Pages for race-series availability. ``apps.events.series`` holds the model behind them.

Five pages: the event's Stages page, where an admin lists the stages; a squad's stage
times, where its captain times each stage; the rider's answers and the captain's results,
reached through the ordinary sheet URLs, which hand series sheets over to here; and the
event-wide series board for coordinators. My Events gets each rider's stages from
``my_series_stages``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import time
from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.decorators import team_member_required
from apps.accounts.discord_service import send_discord_channel_message
from apps.accounts.models import User
from apps.events import rider_stats, series
from apps.events.calendar_utils import race_calendar_urls
from apps.events.context_processors import PENDING_AVAILABILITY_CACHE_PREFIX
from apps.events.forms import SeriesAnswerForm, SeriesStageFormSet, SheetTimesForm, stage_route_choices
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    AvailabilitySlotSelection,
    Event,
    SeriesStage,
    SheetStage,
    Squad,
    SquadMember,
)
from apps.events.squads import squad_member_users as squad_roster_users
from apps.events.views import (
    _can_manage_squad_availability,
    _can_view_v_report,
    _enriched_slot_selections,
    _powerup_choices,
    _slot_modal_selections,
)
from apps.team.services import ZP_DIV_TO_CATEGORY
from apps.zwiftpower.models import ZPTeamRiders

if TYPE_CHECKING:
    from datetime import datetime

    from django.http import HttpRequest, HttpResponse

#: A results cell for each answer state: (label, badge classes). Every state has a word,
#: so none is told apart by colour alone.
CELL = {
    series.ANSWER.AVAILABLE: ("Available", "badge badge-success badge-sm"),
    series.ANSWER.MAYBE: ("Maybe", "badge badge-warning badge-sm"),
    series.ANSWER.NO: ("No", "badge badge-ghost badge-sm"),
    series.STALE: ("Old time", "badge badge-outline badge-sm"),
    series.UNANSWERED: ("No answer", "badge badge-dash badge-sm"),
}

#: What the race dialog says beside a rider who did not answer Available.
MODAL_NOTE = {
    series.ANSWER.MAYBE: "(maybe)",
    series.ANSWER.NO: "(said no)",
    series.STALE: "(answered for the old time)",
    series.UNANSWERED: "(no answer)",
}

#: Badges for ``series.riders_status``.
STATUS_BADGE = {
    "enough": "badge badge-success badge-sm",
    "tight": "badge badge-warning badge-sm",
    "short": "badge badge-error badge-sm",
}

#: Badges for ``series.rider_stage_status`` on My Events.
RIDER_BADGE = {
    "racing": "badge badge-success badge-sm",
    "lineup": "badge badge-info badge-sm",
    "sub": "badge badge-info badge-sm",
    "not_picked": "badge badge-ghost badge-sm",
    "picking": "badge badge-info badge-sm",
    "available": "badge badge-outline badge-sm",
    "maybe": "badge badge-outline badge-sm",
    "no": "badge badge-ghost badge-sm",
    "again": "badge badge-warning badge-sm",
    "unanswered": "badge badge-warning badge-sm",
    "off": "badge badge-ghost badge-sm",
    "untimed": "badge badge-ghost badge-sm",
}


def _can_edit_event(user: User) -> bool:
    """Check whether a user may change an event's setup, its stages included.

    The event-edit gate: global event admins and superusers.

    Args:
        user: The requesting user.

    Returns:
        True if allowed.

    """
    return bool(user.is_event_admin or user.is_superuser)


def _renumber_stages(event: Event) -> None:
    """Renumber an event's stages 0..n-1 in the order the Stages page left them.

    Args:
        event: The race-series event.

    """
    changed = []
    for position, stage in enumerate(sorted(event.series_stages.all(), key=lambda s: (s.order, s.pk))):
        if stage.order != position:
            stage.order = position
            changed.append(stage)
    if changed:
        SeriesStage.objects.bulk_update(changed, ["order"])


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def series_stages_view(request: HttpRequest, pk: int) -> HttpResponse:
    """List, add, edit, reorder and remove a race-series event's stages.

    Gated like the event's own edit page. A stage riders have answered cannot be removed
    (``BaseSeriesStageFormSet.clean``); it can still be renamed or moved to another route.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        The Stages page, or a redirect after saving or when not allowed.

    """
    event = get_object_or_404(Event, pk=pk)
    if not _can_edit_event(request.user):
        logfire.warning("Unauthorized series stages access", event_id=pk, user_id=request.user.id)
        messages.error(request, "You don't have permission to edit this event's stages.")
        return redirect("events:event_detail", pk=pk)

    answered = series.answered_stage_ids(event)
    queryset = event.series_stages.select_related("route")
    kwargs = {
        "instance": event,
        "queryset": queryset,
        "answered_ids": answered,
        "form_kwargs": {"route_choices": stage_route_choices()},
    }
    if request.method == "POST":
        formset = SeriesStageFormSet(request.POST, **kwargs)
        if formset.is_valid():
            with transaction.atomic():
                formset.save()
                _renumber_stages(event)
            logfire.info(
                "Series stages saved",
                event_id=pk,
                user_id=request.user.id,
                added=len(formset.new_objects),
                changed=len(formset.changed_objects),
                removed=len(formset.deleted_objects),
            )
            messages.success(request, "Stages saved.")
            return redirect("events:series_stages", pk=pk)
    else:
        formset = SeriesStageFormSet(initial=[{"order": queryset.count()}], **kwargs)

    rows = [
        {
            "form": form,
            "number": number,
            "answered": bool(form.instance.pk and form.instance.pk in answered),
            "summary": series.route_summary(form.instance) if form.instance.pk else "",
        }
        for number, form in enumerate(formset.forms, 1)
    ]
    return render(
        request,
        "events/series_stages.html",
        {"event": event, "formset": formset, "rows": rows},
    )


def _sheet_initial(
    request: HttpRequest,
    squad: Squad,
    sheet: AvailabilityGrid | None,
    stages: list[SeriesStage],
    existing: dict[int, SheetStage],
) -> dict:
    """Prefill the stage-times form from the sheet, another squad's, or a weekday and time.

    ``?copy_from=<squad pk>`` takes another squad's times (same moments, shown in this
    form's timezone); ``?fill_day=<0-6>&fill_time=HH:MM`` puts every stage on that
    weekday inside its window. Neither saves anything.

    Args:
        request: The HTTP request, for its query string and the user's timezone.
        squad: The squad.
        sheet: The squad's series sheet, if any.
        stages: The event's stages.
        existing: The sheet's stage times by stage pk.

    Returns:
        The form's initial data.

    """
    tz_name = (
        (sheet.grid_timezone if sheet else "") or squad.squad_timezone or getattr(request.user, "timezone", "") or "UTC"
    )
    if series.zone(tz_name).key != tz_name:
        tz_name = "UTC"
    tz = series.zone(tz_name)
    source = existing

    copy_from = request.GET.get("copy_from", "")
    if copy_from.isdigit():
        other = Squad.objects.filter(pk=int(copy_from), event=squad.event).exclude(pk=squad.pk).first()
        other_sheet = series.series_sheet(other) if other else None
        if other_sheet:
            source = {ss.stage_id: ss for ss in other_sheet.sheet_stages.all()}
            messages.info(request, f"Times copied from {other.name}. Nothing is saved until you press Save.")
        else:
            messages.warning(request, "That squad has no stage times to copy.")

    initial: dict = {"timezone": tz_name, "notify_moved": True}
    for stage in stages:
        ss = source.get(stage.pk)
        initial[f"racing_{stage.pk}"] = ss.racing if ss else True
        initial[f"reason_{stage.pk}"] = ss.not_racing_reason if ss else ""
        if ss and ss.starts_at:
            local = ss.starts_at.astimezone(tz)
            initial[f"date_{stage.pk}"] = local.date()
            initial[f"time_{stage.pk}"] = local.time().replace(second=0, microsecond=0)

    fill_day, fill_time = request.GET.get("fill_day", ""), request.GET.get("fill_time", "")
    if fill_day.isdigit() and int(fill_day) < 7 and fill_time:
        try:
            clock = time.fromisoformat(fill_time)
        except ValueError:
            clock = None
        if clock is not None:
            for stage in stages:
                initial[f"time_{stage.pk}"] = clock
                day = series.window_weekday(stage, int(fill_day))
                if day:
                    initial[f"date_{stage.pk}"] = day
            messages.info(
                request,
                "Each stage is filled with that day in its window. Nothing is saved until you press Save.",
            )
    return initial


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def series_sheet_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Set a squad's time for each stage: create its series sheet, or edit it.

    A new sheet is saved as a draft and published from the squad's availability page like
    any other. Published sheets stay editable; moving a stage asks the riders who answered
    it again and moves its scheduled race. Closed sheets are not editable.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.
        squad_pk: The squad primary key.

    Returns:
        The stage-times page, or a redirect after saving or when not allowed.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized series sheet access",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)

    back = redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)
    if not event.is_race_series:
        messages.error(request, "This event collects weekly availability, not stage times.")
        return back
    stages = series.event_stages(event)
    if not stages:
        messages.warning(request, "This event has no stages yet. An event admin adds them on the event's Stages page.")
        return back
    sheet = series.series_sheet(squad)
    if sheet and sheet.is_closed:
        messages.error(request, "This sheet is closed, so its stage times can no longer change.")
        return back
    existing = {ss.stage_id: ss for ss in sheet.sheet_stages.all()} if sheet else {}

    if request.method == "POST":
        form = SheetTimesForm(request.POST, stages=stages)
        if form.is_valid():
            return _save_sheet(request, event, squad, sheet, stages, existing, form)
    else:
        form = SheetTimesForm(initial=_sheet_initial(request, squad, sheet, stages, existing), stages=stages)

    races = series.selections_by_key(sheet) if sheet else {}
    answered: dict[int, int] = defaultdict(int)
    if sheet:
        for answers in sheet.active_responses().values_list("stage_answers", flat=True):
            for key in answers or {}:
                if key.isdigit():
                    answered[int(key)] += 1
    rows = []
    for number, stage in enumerate(stages, 1):
        ss = existing.get(stage.pk)
        rows.append({
            "stage": stage,
            "number": number,
            "route": series.route_summary(stage),
            "window": series.window_label(stage),
            "racing": form[f"racing_{stage.pk}"],
            "date": form[f"date_{stage.pk}"],
            "time": form[f"time_{stage.pk}"],
            "reason": form[f"reason_{stage.pk}"],
            "answered": answered.get(stage.pk, 0),
            "race": races.get(series.slot_key(ss.starts_at)) if ss and ss.is_open else None,
        })

    copy_sources = (
        Squad.objects
        .filter(event=event, availability_grids__kind=AvailabilityGrid.Kind.SERIES)
        .exclude(pk=squad.pk)
        .distinct()
        .order_by("name")
    )
    return render(
        request,
        "events/series_sheet.html",
        {
            "event": event,
            "squad": squad,
            "sheet": sheet,
            "form": form,
            "rows": rows,
            "copy_sources": copy_sources,
            "weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        },
    )


def _save_sheet(
    request: HttpRequest,
    event: Event,
    squad: Squad,
    sheet: AvailabilityGrid | None,
    stages: list[SeriesStage],
    existing: dict[int, SheetStage],
    form: SheetTimesForm,
) -> HttpResponse:
    """Save a squad's stage times, move the races of moved stages, and say what changed.

    Args:
        request: The HTTP request.
        event: The race-series event.
        squad: The squad.
        sheet: The squad's series sheet, or None to create it.
        stages: The event's stages.
        existing: The sheet's stage times by stage pk.
        form: The valid stage-times form.

    Returns:
        A redirect to the squad's availability page.

    """
    cleaned = form.cleaned_data
    tz = series.zone(cleaned["timezone"])
    created = sheet is None
    moves: list[tuple[SeriesStage, datetime, datetime]] = []
    dropped: list[tuple[SeriesStage, datetime]] = []
    with transaction.atomic():
        if sheet is None:
            sheet = AvailabilityGrid(
                squad=squad,
                kind=AvailabilityGrid.Kind.SERIES,
                status=AvailabilityGrid.Status.DRAFT,
                title=f"{event.title} {squad.name} stages",
                created_by=request.user,
            )
        sheet.grid_timezone = cleaned["timezone"]
        rows = []
        for stage in stages:
            ss = existing.get(stage.pk) or SheetStage(stage=stage)
            old = ss.starts_at if ss.pk and ss.is_open else None
            ss.racing = cleaned[f"racing_{stage.pk}"]
            ss.starts_at = form.starts.get(stage.pk)
            ss.not_racing_reason = "" if ss.racing else cleaned[f"reason_{stage.pk}"].strip()
            if old is not None and not ss.is_open:
                dropped.append((stage, old))
            elif old is not None and ss.starts_at != old:
                moves.append((stage, old, ss.starts_at))
            rows.append(ss)
        series.sync_sheet_span(sheet, rows)
        sheet.save()
        for ss in rows:
            ss.grid = sheet
            ss.save()
        moved_races, blocked_races = series.move_scheduled_races(sheet, [(old, new) for _, old, new in moves])

    numbers = {stage.pk: number for number, stage in enumerate(stages, 1)}
    by_stage = {ss.stage_id: ss for ss in rows}
    reask = _riders_to_reask(sheet, by_stage, [stage for stage, _, _ in moves])

    if created:
        messages.success(request, "Stage times saved as a draft. Publish the sheet when you're ready to ask riders.")
    else:
        messages.success(request, "Stage times saved.")
    outside = [
        f"Stage {numbers[stage.pk]} ({series.window_label(stage)})"
        for stage in stages
        if by_stage[stage.pk].is_open and not stage.in_window(by_stage[stage.pk].starts_at.astimezone(tz).date())
    ]
    if outside:
        messages.warning(request, f"Outside the stage window: {', '.join(outside)}.")
    if reask:
        riders = {user.pk for users in reask.values() for user in users}
        text = (
            f"{len(riders)} rider{'s' if len(riders) != 1 else ''} answered a moved stage for its old time "
            "and will be asked again."
        )
        if cleaned["notify_moved"] and sheet.is_published:
            result = _post_stage_moves(request, event, squad, sheet, moves, reask, numbers)
            text += " They were mentioned in the squad's Discord channel." if result is True else f" {result}."
        messages.info(request, text)
    for race in moved_races:
        if race.thread_link:
            messages.warning(
                request, f'"{race.name}" moved with its stage. Post an update in its Discord thread with the new time.'
            )
    for race in blocked_races:
        messages.warning(
            request, f'"{race.name}" stayed at its old time, because another scheduled race already holds the new one.'
        )
    leftovers = series.selections_by_key(sheet) if dropped else {}
    for stage, old in dropped:
        if series.slot_key(old) in leftovers:
            messages.warning(
                request,
                f"Stage {numbers[stage.pk]} is no longer raced, but its scheduled race is still listed. "
                "Delete it on the results page if it won't run.",
            )

    logfire.info(
        "Series sheet saved",
        grid_id=str(sheet.pk),
        squad_id=squad.pk,
        event_id=event.pk,
        user_id=request.user.id,
        created=created,
        open_stages=sum(1 for ss in rows if ss.is_open),
        moved=len(moves),
        races_moved=len(moved_races),
        reasked=len({user.pk for users in reask.values() for user in users}),
    )
    return redirect("events:squad_availability", event_pk=event.pk, squad_pk=squad.pk)


def _riders_to_reask(
    sheet: AvailabilityGrid, by_stage: dict[int, SheetStage], moved: list[SeriesStage]
) -> dict[int, list[User]]:
    """Find the current members whose answer to a moved stage was for its old time.

    Args:
        sheet: The series sheet, saved.
        by_stage: Its stage times by stage pk, as saved.
        moved: The stages whose time changed.

    Returns:
        Riders by stage pk.

    """
    if not moved:
        return {}
    reask: dict[int, list[User]] = defaultdict(list)
    for response in sheet.active_responses().select_related("user"):
        for stage in moved:
            if series.answer_state(response.stage_answers, by_stage[stage.pk]) == series.STALE:
                reask[stage.pk].append(response.user)
    return dict(reask)


def _post_stage_moves(
    request: HttpRequest,
    event: Event,
    squad: Squad,
    sheet: AvailabilityGrid,
    moves: list[tuple[SeriesStage, datetime, datetime]],
    reask: dict[int, list[User]],
    numbers: dict[int, int],
) -> bool | str:
    """Tell the squad's Discord channel which stages moved, mentioning who must answer again.

    Only the riders whose answers went stale are mentioned -- not the squad role -- and
    the mentions are restricted to exactly them, so nothing in a stage name can ping
    anyone else. Times use Discord's own timestamps, so each reader sees their own clock.

    Args:
        request: The HTTP request, for the sheet's absolute URL.
        event: The event.
        squad: The squad whose channel to post in.
        sheet: The series sheet.
        moves: ``(stage, old start, new start)`` for each moved stage.
        reask: Riders to ask again, by stage pk.
        numbers: Stage numbers by stage pk.

    Returns:
        True when posted, otherwise a sentence saying why not.

    """
    if not squad.discord_channel_id:
        return "The squad has no Discord channel to post the change in"
    url = request.build_absolute_uri(
        reverse(
            "events:availability_respond",
            kwargs={"event_pk": event.pk, "squad_pk": squad.pk, "grid_pk": sheet.pk},
        )
    )
    lines = ["**Stage times changed**", sheet.title or event.title]
    lines.extend(
        f"Stage {numbers[stage.pk]}: {stage.name} is now <t:{int(new.timestamp())}:F>" for stage, _old, new in moves
    )
    discord_ids = sorted({str(user.discord_id) for users in reask.values() for user in users if user.discord_id})
    lines.append("")
    if discord_ids:
        lines.append(" ".join(f"<@{discord_id}>" for discord_id in discord_ids) + " you answered for the old time:")
    lines.append(f"Please answer again: {url}")
    with logfire.span(
        "series_stage_moves_notify",
        grid_id=str(sheet.pk),
        squad_id=squad.pk,
        channel_id=str(squad.discord_channel_id),
        mentioned=len(discord_ids),
    ):
        sent = send_discord_channel_message(squad.discord_channel_id, "\n".join(lines), allowed_user_ids=discord_ids)
    if not sent:
        logfire.error("Series stage move notification failed", grid_id=str(sheet.pk), squad_id=squad.pk)
        return "The Discord post failed"
    return True


def series_respond(request: HttpRequest, event: Event, squad: Squad, grid: AvailabilityGrid) -> HttpResponse:
    """Show a rider a series sheet's stages, or save their answers.

    Reached through ``availability_respond_view``, which has already checked the sheet is
    published. Only real answers are stored: a stage left unanswered keeps whatever the
    rider said before, a stale answer included, which is what keeps asking them again.

    Args:
        request: The HTTP request.
        event: The race-series event.
        squad: The squad.
        grid: The squad's series sheet.

    Returns:
        The answer page, or a redirect after saving.

    """
    stages = series.event_stages(event)
    sheet_stages = {ss.stage_id: ss for ss in grid.sheet_stages.all()}
    now = timezone.now()
    # A stage that has started is history: it is shown, but no longer asked about.
    open_ids = [
        stage.pk
        for stage in stages
        if stage.pk in sheet_stages and sheet_stages[stage.pk].is_open and sheet_stages[stage.pk].starts_at > now
    ]
    response = AvailabilityResponse.objects.filter(grid=grid, user=request.user).first()
    answers = dict(response.stage_answers or {}) if response else {}
    race_verified_required = bool(event.require_race_verified_availability or grid.require_race_verified_availability)
    user_is_race_ready = bool(getattr(request.user, "is_race_ready", False))
    here = redirect("events:availability_respond", event_pk=event.pk, squad_pk=squad.pk, grid_pk=grid.pk)

    if request.method == "POST":
        if race_verified_required and not user_is_race_ready:
            logfire.warning(
                "Availability submission blocked: race verified required",
                grid_id=str(grid.id),
                squad_id=squad.pk,
                event_id=event.pk,
                user_id=request.user.id,
            )
            messages.error(request, "You must be Race Verified to submit availability for this event.")
            return here
        form = SeriesAnswerForm(request.POST, stage_ids=open_ids)
        if form.is_valid():
            given = 0
            for pk in open_ids:
                choice = form.cleaned_data[f"answer_{pk}"]
                if not choice:
                    continue
                entry = {"answer": choice, "for": series.stamp(sheet_stages[pk].starts_at)}
                if form.cleaned_data[f"note_{pk}"]:
                    entry["note"] = form.cleaned_data[f"note_{pk}"]
                answers[str(pk)] = entry
                given += 1
            if given or response is not None:
                AvailabilityResponse.objects.update_or_create(
                    grid=grid, user=request.user, defaults={"stage_answers": answers}
                )
                # The badge would otherwise keep asking for up to a minute after the answer.
                cache.delete(f"{PENDING_AVAILABILITY_CACHE_PREFIX}:{request.user.pk}")
                outstanding = sum(
                    1
                    for pk in open_ids
                    if series.answer_state(answers, sheet_stages[pk]) in {series.UNANSWERED, series.STALE}
                )
                logfire.info(
                    "Series availability saved",
                    grid_id=str(grid.id),
                    squad_id=squad.pk,
                    event_id=event.pk,
                    user_id=request.user.id,
                    answered=given,
                    outstanding=outstanding,
                )
                if outstanding:
                    messages.success(
                        request,
                        f"Availability saved. {outstanding} stage{'s' if outstanding != 1 else ''} still "
                        f"need{'' if outstanding != 1 else 's'} an answer.",
                    )
                else:
                    messages.success(request, "Availability saved.")
                return here
            messages.warning(request, "Choose Available, Maybe or No for at least one stage.")
    else:
        form = SeriesAnswerForm(stage_ids=open_ids)

    user_tz = getattr(request.user, "timezone", "") or ""
    tz = series.zone(user_tz or grid.grid_timezone)
    squad_tz = series.zone(grid.grid_timezone)
    rows = []
    for number, stage in enumerate(stages, 1):
        ss = sheet_stages.get(stage.pk)
        row = {"number": number, "stage": stage, "route": series.route_summary(stage)}
        if ss is not None and not ss.racing:
            row.update({"status": "off", "reason": ss.not_racing_reason})
        elif ss is None or not ss.is_open:
            row["status"] = "untimed"
        elif ss.starts_at <= now:
            state = series.answer_state(answers, ss)
            row.update({
                "status": "past",
                "when": series.when_label(ss.starts_at, tz),
                "answer_label": CELL[state][0] if state in series.ANSWER_VALUES else "",
            })
        else:
            state = series.answer_state(answers, ss)
            entry = answers.get(str(stage.pk)) or {}
            answer_field, note_field = form[f"answer_{stage.pk}"], form[f"note_{stage.pk}"]
            row.update({
                "status": "open",
                "when": series.when_label(ss.starts_at, tz),
                "squad_when": (
                    f"{ss.starts_at.astimezone(squad_tz):%H:%M} {squad_tz.key}" if squad_tz.key != tz.key else ""
                ),
                "selected": answer_field.value() if form.is_bound else (state if state in series.ANSWER_VALUES else ""),
                "stale_answer": series.ANSWER(entry["answer"]).label if state == series.STALE else "",
                "note": note_field.value() if form.is_bound else entry.get("note", ""),
                "errors": [*answer_field.errors, *note_field.errors],
            })
        rows.append(row)
    open_rows = [row for row in rows if row["status"] == "open"]

    logfire.debug(
        "Series respond page viewed",
        grid_id=str(grid.id),
        user_id=request.user.id,
        event_id=event.pk,
        squad_id=squad.pk,
    )
    return render(
        request,
        "events/availability_respond_series.html",
        {
            "event": event,
            "squad": squad,
            "grid": grid,
            "rows": rows,
            "answer_choices": series.ANSWER.choices,
            "open_count": len(open_rows),
            "answered_count": sum(1 for row in open_rows if row["selected"]),
            "stale_count": sum(1 for row in open_rows if row["stale_answer"] and not row["selected"]),
            "display_timezone": tz.key,
            "tz_is_default": not user_tz,
            "note_max_length": series.NOTE_MAX_LENGTH,
            "race_verified_required": race_verified_required,
            "user_is_race_ready": user_is_race_ready,
            "can_manage_availability": _can_manage_squad_availability(request.user, squad),
        },
    )


def _enrich_members(users: list[User]) -> list[dict]:
    """Add each rider's ZwiftPower and Zwift Racing figures for the rider popover.

    Args:
        users: The riders.

    Returns:
        One dict per rider, in order, with the keys ``accounts/_user_tooltip.html`` reads.

    """
    zwids = [u.zwid for u in users if u.zwid]
    zp_by_zwid = {r.zwid: r for r in ZPTeamRiders.objects.filter(zwid__in=zwids)} if zwids else {}
    profiles = rider_stats.profiles_by_zwid(zwids)
    enriched = []
    for user in users:
        zp = zp_by_zwid.get(user.zwid)
        zr = rider_stats.zr_fields(profiles.get(user.zwid) if user.zwid else None, gender=user.gender)
        enriched.append({
            "user": user,
            "display_name": user.get_full_name() or user.discord_username,
            "zwid": user.zwid,
            "is_race_ready": user.is_race_ready,
            "is_extra_verified": user.is_extra_verified,
            "in_zwiftpower": zp is not None,
            "zp_category": ZP_DIV_TO_CATEGORY.get(zp.div, "") if zp and zp.div else "",
            "zp_category_w": ZP_DIV_TO_CATEGORY.get(zp.divw, "") if zp and zp.divw else "",
            **{key: zr[key] for key in rider_stats.TOOLTIP_FIELDS},
        })
    return enriched


def _race_label(race: AvailabilitySlotSelection | None) -> str:
    """Summarise a stage's scheduled race for the results footer.

    Args:
        race: The race, riders prefetched, or None.

    Returns:
        "Not picked", or the race's status and rider count.

    """
    if race is None:
        return "Not picked"
    riders = len(race.selected_users.all())
    count = f"{riders} rider{'s' if riders != 1 else ''}"
    if race.status == AvailabilitySlotSelection.Status.NONE:
        return count
    return f"{race.get_status_display()} · {count}"


def series_results(request: HttpRequest, event: Event, squad: Squad, grid: AvailabilityGrid) -> HttpResponse:
    """Show a squad's answers stage by stage, and let its captains pick each stage's lineup.

    Reached through ``availability_results_view``, which has already checked the sheet is
    published or closed. Visible to any team member, like a weekly sheet's results; riders'
    notes and the lineup controls only to those who manage the squad.

    Args:
        request: The HTTP request.
        event: The race-series event.
        squad: The squad.
        grid: The squad's series sheet.

    Returns:
        The results page.

    """
    stages = series.event_stages(event)
    sheet_stages = {ss.stage_id: ss for ss in grid.sheet_stages.all()}
    is_captain = _can_manage_squad_availability(request.user, squad)
    user_tz = getattr(request.user, "timezone", "") or ""
    tz = series.zone(user_tz or grid.grid_timezone)

    members = list(
        User.objects.filter(
            squad_memberships__squad=squad, squad_memberships__status=SquadMember.Status.MEMBER
        ).order_by("first_name", "last_name")
    )
    member_ids = [user.pk for user in members]
    answers_by_user = {r.user_id: r.stage_answers or {} for r in grid.active_responses()}
    races = series.selections_by_key(grid)

    columns = []
    for number, stage in enumerate(stages, 1):
        ss = sheet_stages.get(stage.pk)
        column = {"number": number, "stage": stage, "route": series.route_summary(stage), "ss": ss}
        if ss is not None and ss.is_open:
            utc_date, utc_time = series.slot_key(ss.starts_at)
            counts = series.tally(ss, answers_by_user, member_ids)
            status = series.riders_status(stage, counts)
            race = races.get((utc_date, utc_time))
            column.update({
                "is_open": True,
                "when": series.when_label(ss.starts_at, tz),
                "utc_date": utc_date.isoformat(),
                "utc_time": utc_time,
                "key": f"{utc_date.isoformat()}|{utc_time}",
                "counts": counts,
                "status": status[1] if status else "",
                "status_class": STATUS_BADGE[status[0]] if status else "",
                "race": race,
                "race_label": _race_label(race),
            })
        else:
            column.update({
                "is_open": False,
                "when": "Not racing" if ss is not None and not ss.racing else "No time yet",
                "reason": ss.not_racing_reason if ss is not None and not ss.racing else "",
            })
        columns.append(column)
    open_columns = [column for column in columns if column["is_open"]]

    enriched = _enrich_members(members)
    rows, notes = [], []
    for entry in enriched:
        user = entry["user"]
        answers = answers_by_user.get(user.pk)
        cells = []
        for column in columns:
            if not column["is_open"]:
                cells.append({"label": "—", "badge": ""})
                continue
            state = series.answer_state(answers, column["ss"])
            label, badge = CELL[state]
            cells.append({"label": label, "badge": badge})
            note = ((answers or {}).get(str(column["stage"].pk)) or {}).get("note", "")
            if is_captain and note:
                notes.append({
                    "rider": entry["display_name"],
                    "stage": f"Stage {column['number']}: {column['stage'].name}",
                    "answer": label,
                    "note": note,
                })
        rows.append({**entry, "cells": cells, "pending": series.needs_answer(sheet_stages.values(), answers)})

    modal: dict = {}
    if is_captain:
        cell_users: dict[str, list[int]] = {}
        cell_notes: dict[str, dict[str, str]] = {}
        for column in open_columns:
            cell_users[column["key"]] = []
            cell_notes[column["key"]] = {}
            for user in members:
                state = series.answer_state(answers_by_user.get(user.pk), column["ss"])
                if state == series.ANSWER.AVAILABLE:
                    cell_users[column["key"]].append(user.pk)
                else:
                    cell_notes[column["key"]][str(user.pk)] = MODAL_NOTE[state]
        user_data = {
            entry["user"].pk: {
                "display_name": entry["display_name"],
                "zr_category": entry["zr_category"],
                "zr_rating": float(entry["zr_rating"]) if entry["zr_rating"] is not None else None,
                "is_race_ready": entry["is_race_ready"],
            }
            for entry in enriched
        }
        # Captains race too, and are not always squad MEMBER rows.
        roster = squad_roster_users(squad)
        for entry in _enrich_members([user for user in roster if user.pk not in user_data]):
            user_data[entry["user"].pk] = {
                "display_name": entry["display_name"],
                "zr_category": entry["zr_category"],
                "zr_rating": float(entry["zr_rating"]) if entry["zr_rating"] is not None else None,
                "is_race_ready": entry["is_race_ready"],
            }
        modal = {
            "utc_cell_users_json": cell_users,
            "utc_cell_notes_json": cell_notes,
            "user_data_json": user_data,
            "squad_roster_ids_json": [user.pk for user in roster],
            "selections_json": _slot_modal_selections(list(races.values())),
            "slot_defaults_json": {
                column["key"]: {
                    "name": f"Stage {column['number']}: {column['stage'].name}",
                    "laps": column["stage"].laps if column["stage"].route_id else None,
                    "course_url": (
                        request.build_absolute_uri(reverse("routes:detail", args=[column["stage"].route.name_hash]))
                        if column["stage"].route_id
                        else ""
                    ),
                }
                for column in open_columns
            },
            "powerup_choices": _powerup_choices(),
        }

    slot_selections_enriched, _ = _enriched_slot_selections(request, grid)
    logfire.debug(
        "Series results viewed",
        grid_id=str(grid.id),
        user_id=request.user.id,
        event_id=event.pk,
        squad_id=squad.pk,
    )
    return render(
        request,
        "events/availability_results_series.html",
        {
            "event": event,
            "squad": squad,
            "grid": grid,
            "columns": columns,
            "rows": rows,
            "notes": notes,
            "member_count": len(members),
            "complete_count": sum(1 for row in rows if not row["pending"]),
            "unanswered": [row["display_name"] for row in rows if row["user"].pk not in answers_by_user],
            "stale_count": sum(column["counts"].stale for column in open_columns),
            "display_timezone": tz.key,
            "tz_is_default": not user_tz,
            "is_event_admin": is_captain,
            "slot_selections_enriched": slot_selections_enriched,
            "schedule_hint": "Use a stage's Lineup button to schedule its race.",
            **modal,
        },
    )


@login_required
@team_member_required()
@require_GET
def series_board_view(request: HttpRequest, pk: int) -> HttpResponse:
    """Show every squad's time, answers and lineup for every stage of a race series.

    For the people running the event: gated like the Eligibility page, the set that already
    sees its internals. Answers count current squad members only.

    Args:
        request: The HTTP request. ``?tz=mine`` shows every time in the viewer's timezone
            rather than each squad's own.
        pk: The event primary key.

    Returns:
        The board, or a redirect when not allowed or not a series.

    """
    event = get_object_or_404(Event, pk=pk)
    if not _can_view_v_report(request.user, event):
        logfire.warning("Unauthorized series board access", event_id=pk, user_id=request.user.id)
        messages.error(request, "You don't have permission to view the series board.")
        return redirect("events:event_detail", pk=pk)
    if not event.is_race_series:
        messages.info(request, "This event collects weekly availability, so it has no series board.")
        return redirect("events:event_detail", pk=pk)

    stages = series.event_stages(event)
    squads = list(event.squads.order_by("name"))
    sheets: dict[int, AvailabilityGrid] = {}
    for grid in (
        AvailabilityGrid.objects
        .filter(squad__event=event, kind=AvailabilityGrid.Kind.SERIES)
        .prefetch_related("sheet_stages")
        .order_by("created_at")
    ):
        sheets.setdefault(grid.squad_id, grid)
    members: dict[int, list[int]] = defaultdict(list)
    for squad_id, user_id in SquadMember.objects.filter(
        squad__event=event, status=SquadMember.Status.MEMBER
    ).values_list("squad_id", "user_id"):
        members[squad_id].append(user_id)
    answers: dict = defaultdict(dict)
    for grid_id, user_id, stage_answers in AvailabilityResponse.objects.filter(
        grid__in=list(sheets.values())
    ).values_list("grid_id", "user_id", "stage_answers"):
        answers[grid_id][user_id] = stage_answers or {}
    races = {
        (race.grid_id, race.slot_date, race.slot_time): race
        for race in AvailabilitySlotSelection.objects.filter(grid__in=list(sheets.values())).annotate(
            rider_count=Count("selected_users")
        )
    }
    mine = request.GET.get("tz") == "mine"
    viewer_tz = series.zone(getattr(request.user, "timezone", "") or "UTC")

    racing = [0] * len(stages)
    rows, attention = [], []
    for squad in squads:
        sheet = sheets.get(squad.pk)
        tz = viewer_tz if mine else series.zone((sheet.grid_timezone if sheet else "") or squad.squad_timezone)
        link = (
            reverse("events:availability_results", args=[event.pk, squad.pk, sheet.pk])
            if sheet and not sheet.is_draft
            else ""
        )
        if sheet is None:
            attention.append({
                "tag": "No sheet",
                "cls": "badge badge-dash badge-sm",
                "text": f"{squad.name} hasn't set its stage times yet.",
                "url": "",
            })
        elif sheet.is_draft:
            attention.append({
                "tag": "Draft",
                "cls": "badge badge-dash badge-sm",
                "text": f"{squad.name}'s stage times are still a draft, so its riders haven't been asked.",
                "url": "",
            })
        by_stage = {ss.stage_id: ss for ss in sheet.sheet_stages.all()} if sheet else {}
        cells = []
        for index, stage in enumerate(stages):
            ss = by_stage.get(stage.pk)
            cell = {"when": "—", "counts": "", "chip": "", "chip_class": "", "extra": ""}
            if sheet is None:
                cell.update({"chip": "No sheet", "chip_class": "badge badge-dash badge-sm"})
            elif ss is not None and not ss.racing:
                cell.update({
                    "when": "Not racing",
                    "chip": "Not racing",
                    "chip_class": "badge badge-ghost badge-sm",
                    "extra": ss.not_racing_reason,
                })
            elif ss is None or not ss.is_open:
                cell.update({"when": "No time yet", "chip": "Not set", "chip_class": "badge badge-dash badge-sm"})
            else:
                racing[index] += 1
                cell["when"] = series.when_label(ss.starts_at, tz)
                if sheet.is_draft:
                    cell.update({"chip": "Draft", "chip_class": "badge badge-dash badge-sm"})
                else:
                    counts = series.tally(ss, answers[sheet.pk], members.get(squad.pk, []))
                    cell["counts"] = f"{counts.available} available · {counts.maybe} maybe"
                    race = races.get((sheet.pk, *series.slot_key(ss.starts_at)))
                    status = series.riders_status(stage, counts)
                    if race is not None and race.status == AvailabilitySlotSelection.Status.CONFIRMED:
                        cell.update({
                            "chip": f"Lineup set · {race.rider_count}",
                            "chip_class": "badge badge-success badge-sm",
                        })
                    elif race is not None:
                        cell.update({
                            "chip": f"Picking · {race.rider_count}",
                            "chip_class": "badge badge-info badge-sm",
                        })
                    elif status:
                        cell.update({"chip": status[1], "chip_class": STATUS_BADGE[status[0]]})
                    else:
                        cell.update({"chip": "Open", "chip_class": "badge badge-outline badge-sm"})
                    if counts.stale:
                        cell["extra"] = f"↻ {counts.stale} to re-confirm"
                        attention.append({
                            "tag": "Re-confirm",
                            "cls": "badge badge-warning badge-sm",
                            "text": f"{squad.name}, Stage {index + 1}: {counts.stale} rider"
                            f"{'s' if counts.stale != 1 else ''} still to answer its new time.",
                            "url": link,
                        })
                    if race is None and status and status[0] == "short":
                        attention.append({
                            "tag": "Short",
                            "cls": "badge badge-error badge-sm",
                            "text": f"{squad.name}, Stage {index + 1} ({stage.name}): "
                            f"{counts.available + counts.maybe} available or maybe; it needs {stage.riders_min}.",
                            "url": link,
                        })
            cells.append(cell)
        rows.append({
            "squad": squad,
            "sheet": sheet,
            "link": link,
            "tz": tz.key,
            "member_count": len(members.get(squad.pk, [])),
            "cells": cells,
        })

    columns = [
        {
            "number": index + 1,
            "stage": stage,
            "route": series.route_summary(stage),
            "window": series.window_label(stage),
            "racing": f"{racing[index]} of {len(squads)} racing",
        }
        for index, stage in enumerate(stages)
    ]
    logfire.debug("Series board viewed", event_id=pk, user_id=request.user.id, squads=len(squads))
    return render(
        request,
        "events/series_board.html",
        {
            "event": event,
            "columns": columns,
            "rows": rows,
            "attention": attention,
            "mine": mine,
            "viewer_timezone": viewer_tz.key,
        },
    )


def my_series_stages(request: HttpRequest, grids: list[AvailabilityGrid]) -> dict[int, dict]:
    """Lay out each series sheet a rider is on as their own list of stages, for My Events.

    Args:
        request: The HTTP request, for the rider and the calendar links.
        grids: The published or closed sheets of the rider's squads; weekly ones are skipped.

    Returns:
        ``{squad pk: {"grid", "stages", "needs_answer"}}``.

    """
    sheets = [grid for grid in grids if grid.is_series]
    if not sheets:
        return {}
    user = request.user
    answers = {
        grid_id: stage_answers or {}
        for grid_id, stage_answers in AvailabilityResponse.objects.filter(user=user, grid__in=sheets).values_list(
            "grid_id", "stage_answers"
        )
    }
    stages_by_event: dict[int, list[SeriesStage]] = {}
    now = timezone.now()
    result = {}
    for sheet in sheets:
        event = sheet.squad.event
        if event.pk not in stages_by_event:
            stages_by_event[event.pk] = series.event_stages(event)
        tz = series.zone(getattr(user, "timezone", "") or sheet.grid_timezone)
        races = series.selections_by_key(sheet)
        by_stage = {ss.stage_id: ss for ss in sheet.sheet_stages.all()}
        rows = []
        for number, stage in enumerate(stages_by_event[event.pk], 1):
            ss = by_stage.get(stage.pk)
            race = races.get(series.slot_key(ss.starts_at)) if ss is not None and ss.is_open else None
            if ss is None:
                kind, label = "untimed", "Time not set yet"
            else:
                kind, label = series.rider_stage_status(ss, answers.get(sheet.pk), race, user.pk)
            # Links only for a race the rider is in: the thread and invite are theirs to use.
            mine = race if kind in {"racing", "lineup", "sub"} else None
            local = ss.starts_at.astimezone(tz) if ss is not None and ss.is_open else None
            rows.append({
                "number": number,
                "stage": stage,
                "kind": kind,
                "label": label,
                "badge": RIDER_BADGE[kind],
                "dow": f"{local:%a}" if local else "",
                "day": local.day if local else "",
                "month": f"{local:%b}" if local else "",
                "time": f"{local:%H:%M}" if local else "",
                "reason": ss.not_racing_reason if ss is not None and not ss.racing else "",
                "race": mine,
                **(race_calendar_urls(mine, request) if mine is not None else {}),
            })
        result[sheet.squad_id] = {
            "grid": sheet,
            "stages": rows,
            "needs_answer": sheet.is_published
            and series.needs_answer(by_stage.values(), answers.get(sheet.pk), after=now),
        }
    return result
