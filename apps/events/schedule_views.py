"""A squad's availability posting schedule: the page that sets it up, its preview and its buttons.

``apps.events.availability_schedules`` does the posting; this only edits the schedule. Who may
edit it is who may manage the squad's availability (captains, vice-captains, event admins).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import team_member_required
from apps.events import availability_schedules, series
from apps.events.forms import SCHEDULE_REMINDER_DAYS, AvailabilityScheduleForm
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityGridTemplate,
    AvailabilitySchedule,
    Event,
    Squad,
    SquadMember,
)
from apps.events.views import _can_manage_squad_availability

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

#: What a new schedule starts with: Friday evening, for the week after next Friday, reminders
#: on Sunday and Tuesday, closed on Thursday so the next one has the stage to itself.
NEW_SCHEDULE_DEFAULTS = {
    "weekday": 4,
    "post_time": "18:00",
    "starts_after_days": 7,
    "reminder_days": [2, 4],
    "close_after_days": 6,
    "publish_mode": AvailabilitySchedule.PublishMode.PUBLISH,
}


def _squad_or_refuse(request: HttpRequest, event_pk: int, squad_pk: int) -> tuple[Event, Squad] | HttpResponse:
    """Load the event and squad, or say why this user may not edit the squad's schedule.

    Returns:
        ``(event, squad)``, or the redirect to send instead.

    """
    event = get_object_or_404(Event, pk=event_pk)
    squad = get_object_or_404(Squad, pk=squad_pk, event=event)
    if not _can_manage_squad_availability(request.user, squad):
        logfire.warning(
            "Unauthorized availability schedule access",
            squad_id=squad_pk,
            event_id=event_pk,
            user_id=request.user.id,
        )
        messages.error(request, "You don't have permission to manage availability.")
        return redirect("events:event_detail", pk=event_pk)
    if event.is_race_series:
        messages.info(request, "This event collects availability per stage, so it has no weekly schedule.")
        return redirect("events:squad_availability", event_pk=event_pk, squad_pk=squad_pk)
    return event, squad


def _initial(squad: Squad) -> dict:
    """Give the starting values for a squad's first schedule.

    The timezone is the squad's own when it names a real zone, else its first template's.

    Returns:
        Initial form values.

    """
    template = AvailabilityGridTemplate.objects.filter(squad=squad).order_by("name").first()
    named = squad.squad_timezone
    tz = named if named and series.zone(named).key == named else ""
    return {
        **NEW_SCHEDULE_DEFAULTS,
        "template": template,
        "schedule_timezone": tz or (template.grid_timezone if template else "UTC"),
    }


def _history(schedule: AvailabilitySchedule) -> list[AvailabilityGrid]:
    """List the sheets the schedule has made, newest first, each with its answer count.

    Returns:
        Up to eight sheets.

    """
    return list(
        schedule.grids.annotate(
            answered=Count(
                "responses",
                filter=Q(
                    responses__user__squad_memberships__squad=schedule.squad,
                    responses__user__squad_memberships__status=SquadMember.Status.MEMBER,
                ),
            )
        ).order_by("-scheduled_for")[:8]
    )


def _preview_context(schedule: AvailabilitySchedule) -> dict:
    now = timezone.now()
    return {
        "preview_schedule": schedule,
        "upcoming": availability_schedules.upcoming(schedule, now) if schedule.template_id else [],
        "now": now,
        "tz": availability_schedules.zone(schedule.schedule_timezone),
    }


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def availability_schedule_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Set up, or change, the squad's weekly posting schedule.

    Saving sets ``not_before`` to now, so a change made after this week's post time waits for
    next week rather than posting at once, and remembers the site's address for the links
    the job posts.

    Args:
        request: The HTTP request.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        The schedule page, or a redirect after saving.

    """
    loaded = _squad_or_refuse(request, event_pk, squad_pk)
    if not isinstance(loaded, tuple):
        return loaded
    event, squad = loaded
    schedule = AvailabilitySchedule.objects.filter(squad=squad).select_related("template").first()

    if request.method == "POST":
        form = AvailabilityScheduleForm(
            request.POST, instance=schedule or AvailabilitySchedule(squad=squad), squad=squad
        )
        if form.is_valid():
            saved = form.save(commit=False)
            saved.squad = squad
            saved.not_before = timezone.now()
            saved.base_url = request.build_absolute_uri("/")
            saved.updated_by = request.user
            if saved.skip_date and saved.skip_date < timezone.now().date():
                saved.skip_date = None
            created = saved.pk is None
            saved.save()
            logfire.info(
                "Availability schedule saved",
                schedule_id=saved.pk,
                squad_id=squad.pk,
                event_id=event.pk,
                user_id=request.user.id,
                created=created,
            )
            when = availability_schedules.next_post(saved, timezone.now())
            if when and saved.active:
                local = when.astimezone(availability_schedules.zone(saved.schedule_timezone))
                messages.success(request, f"Schedule saved. The next sheet posts {local:%a %-d %b at %H:%M %Z}.")
            else:
                messages.success(request, "Schedule saved.")
            return redirect("events:availability_schedule", event_pk=event.pk, squad_pk=squad.pk)
    else:
        form = AvailabilityScheduleForm(instance=schedule, initial=None if schedule else _initial(squad), squad=squad)

    preview_schedule = schedule
    if preview_schedule is None and form.initial.get("template"):
        preview_schedule = AvailabilitySchedule(squad=squad, **{**form.initial, "not_before": timezone.now()})

    return render(
        request,
        "events/availability_schedule.html",
        {
            "event": event,
            "squad": squad,
            "schedule": schedule,
            "form": form,
            "reminder_days": SCHEDULE_REMINDER_DAYS,
            "has_templates": form.fields["template"].queryset.exists(),
            "history": _history(schedule) if schedule else [],
            "next_post": availability_schedules.next_post(schedule, timezone.now()) if schedule else None,
            **_preview_context(preview_schedule or AvailabilitySchedule(squad=squad)),
        },
    )


@login_required
@team_member_required()
@require_POST
def availability_schedule_preview_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Redraw the "next sheets" preview from the form as it stands, before it is saved.

    Args:
        request: The HTMX request carrying the form.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        The preview partial.

    """
    loaded = _squad_or_refuse(request, event_pk, squad_pk)
    if not isinstance(loaded, tuple):
        return loaded
    _event, squad = loaded
    existing = AvailabilitySchedule.objects.filter(squad=squad).first()
    form = AvailabilityScheduleForm(request.POST, instance=existing or AvailabilitySchedule(squad=squad), squad=squad)
    draft = None
    if form.is_valid():
        draft = form.save(commit=False)
        draft.not_before = timezone.now()
    return render(
        request,
        "events/_availability_schedule_preview.html",
        {
            "squad": squad,
            "form_errors": not form.is_valid(),
            **_preview_context(draft or AvailabilitySchedule(squad=squad)),
        },
    )


@login_required
@team_member_required()
@require_POST
def availability_schedule_action_view(request: HttpRequest, event_pk: int, squad_pk: int) -> HttpResponse:
    """Pause, resume, skip the next post, undo a skip, or delete the schedule.

    Resuming, like saving, waits for the next post time rather than catching up on a post
    missed while paused. Deleting keeps the sheets it made; they just stop getting reminders
    and closing on their own.

    Args:
        request: The HTTP request with an ``action`` field.
        event_pk: The parent event primary key.
        squad_pk: The squad primary key.

    Returns:
        Redirect to the schedule page (or, after deleting, the availability page).

    """
    loaded = _squad_or_refuse(request, event_pk, squad_pk)
    if not isinstance(loaded, tuple):
        return loaded
    event, squad = loaded
    schedule = get_object_or_404(AvailabilitySchedule, squad=squad)
    action = request.POST.get("action", "")
    now = timezone.now()
    back = redirect("events:availability_schedule", event_pk=event.pk, squad_pk=squad.pk)

    if action == "pause":
        schedule.active = False
        schedule.save(update_fields=["active", "updated_at"])
        messages.success(request, "Schedule paused. Nothing is posted, reminded or closed until you resume it.")
    elif action == "resume":
        schedule.active = True
        schedule.not_before = now
        schedule.base_url = request.build_absolute_uri("/")
        schedule.save(update_fields=["active", "not_before", "base_url", "updated_at"])
        messages.success(request, "Schedule resumed.")
    elif action == "skip":
        coming = next((u for u in availability_schedules.upcoming(schedule, now) if not u.skipped), None)
        if coming is None:
            messages.error(request, "There is no coming post to skip.")
            return back
        tz = availability_schedules.zone(schedule.schedule_timezone)
        schedule.skip_date = coming.post_at.astimezone(tz).date()
        schedule.save(update_fields=["skip_date", "updated_at"])
        messages.success(request, f"The {schedule.skip_date:%a %-d %b} sheet will be skipped.")
    elif action == "unskip":
        schedule.skip_date = None
        schedule.save(update_fields=["skip_date", "updated_at"])
        messages.success(request, "The skipped week will post after all.")
    elif action == "delete":
        schedule.delete()
        messages.success(request, "Schedule deleted. The sheets it made are still on the availability page.")
        return redirect("events:squad_availability", event_pk=event.pk, squad_pk=squad.pk)
    else:
        messages.error(request, "Unknown action.")
        return back

    logfire.info(
        "Availability schedule action",
        action=action,
        schedule_id=schedule.pk,
        squad_id=squad.pk,
        event_id=event.pk,
        user_id=request.user.id,
    )
    return back
