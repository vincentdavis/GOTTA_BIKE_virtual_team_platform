"""Pages for signup statuses. ``apps.events.signup_status`` holds the rules behind them.

Two pages:

- **A signup's status** (``signup_status_view``), opened from the Status cell on the event
  page's signup table and on Assign Riders. Those pages fetch it into their one status
  dialog (``HX-Target: status-dialog-body``); without JavaScript the same URL is a
  page of its own. Saving is an ordinary form post that goes back where it came from.
- **An event's statuses** (``signup_statuses_view``), linked from the event's edit page: the
  list staff choose from on that event, empty until someone adds to it or copies another
  event's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import team_member_required
from apps.accounts.models import GuildMember
from apps.events import signup_status
from apps.events.forms import SignupStatusForm
from apps.events.models import Event, EventSignup, SignupStatus, Squad, SquadMember
from apps.events.signup_requirements import requirement_phrase, signup_blockers
from apps.events.views import _can_manage_event_squads, _can_manage_signup_questions

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User

#: The id of the box the status dialog fetches a signup's form into.
DIALOG_BODY_ID = "status-dialog-body"

#: The status dialog's choices besides a status from the list: no status, and withdrawn. A
#: rider who has left the team also gets "keep", which saves the note and nothing else.
NO_STATUS = ""
WITHDRAW = "withdrawn"
KEEP = "keep"

NOTE_MAX_LENGTH = 500


def _name(user: User) -> str:
    """Name a rider the way the signup tables do.

    Args:
        user: The rider.

    Returns:
        Their full name, or their Discord username.

    """
    return user.get_full_name() or user.discord_username or f"Rider {user.pk}"


def _safe_next(request: HttpRequest, fallback: str) -> str:
    """Read where to go back to, refusing anywhere off this site.

    Args:
        request: The request, carrying ``next`` in its query or form.
        fallback: Where to go when ``next`` is missing or unsafe.

    Returns:
        A path or URL on this site.

    """
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


def _member_squads(event: Event, user: User) -> list[Squad]:
    """List the squads of an event a rider is a member of.

    Args:
        event: The event.
        user: The rider.

    Returns:
        The squads, by name.

    """
    return list(
        Squad.objects.filter(
            event=event, squad_members__user=user, squad_members__status=SquadMember.Status.MEMBER
        ).order_by("name")
    )


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def signup_status_view(request: HttpRequest, event_pk: int, signup_pk: int) -> HttpResponse:
    """Show or set one signup's status and note.

    Gated like squad management (``_can_manage_event_squads``): event admins, superusers, and
    the event's head captain and coordinator roles. The note is theirs alone, as the riders'
    own notes are. A refused fetch into the dialog gets a 403, never a redirect: htmx would
    follow it and fill the dialog with the page it lands on.

    Args:
        request: The HTTP request.
        event_pk: The event primary key.
        signup_pk: The signup primary key.

    Returns:
        The form (in the dialog, or as a page), or a redirect after saving or when refused.

    """
    event = get_object_or_404(Event, pk=event_pk)
    signup = get_object_or_404(EventSignup.objects.select_related("user", "status_set_by"), pk=signup_pk, event=event)
    htmx = getattr(request, "htmx", None)
    embedded = bool(htmx and htmx.target == DIALOG_BODY_ID)
    next_url = _safe_next(request, reverse("events:event_detail", args=[event.pk]) + "?tab=signups")

    if not _can_manage_event_squads(request.user, event):
        logfire.warning(
            "Unauthorized signup status access",
            event_id=event.pk,
            signup_id=signup.pk,
            user_id=request.user.id,
            embedded=embedded,
        )
        if embedded:
            return HttpResponseForbidden()
        messages.error(request, "You don't have permission to set signup statuses.")
        return redirect("events:event_detail", pk=event.pk)

    departed = bool(signup_status.departed_user_ids([signup.user]))
    squads = _member_squads(event, signup.user)
    if request.method == "POST":
        _save(request, event, signup, departed=departed, squads=squads)
        return redirect(next_url)

    current = SignupStatus.objects.filter(pk=signup.staff_status_id).first() if signup.staff_status_id else None
    offered = signup_status.offered_statuses(event, current=current)
    squad_timezones = set(event.squads.exclude(squad_timezone="").values_list("squad_timezone", flat=True))
    withdrawn = signup.status == EventSignup.Status.WITHDRAWN
    if withdrawn:
        choice = WITHDRAW
    elif current is not None:
        choice = str(current.pk)
    else:
        choice = NO_STATUS
    left_on = None
    if departed:
        left_on = (
            GuildMember.objects
            .filter(discord_id=str(signup.user.discord_id), date_left__isnull=False)
            .values_list("date_left", flat=True)
            .first()
        )
    logfire.debug("Signup status form viewed", event_id=event.pk, signup_id=signup.pk, user_id=request.user.id)
    response = render(
        request,
        "events/_signup_status_form.html" if embedded else "events/signup_status.html",
        {
            "event": event,
            "signup": signup,
            "name": _name(signup.user),
            "embedded": embedded,
            "next": next_url,
            "departed": departed,
            "left_on": left_on,
            "withdrawn": withdrawn,
            "squads": squads,
            "choice": choice,
            "waiting_statuses": [s for s in offered if s.is_waiting],
            "set_aside_statuses": [s for s in offered if not s.is_waiting],
            "unmatched_timezones": signup_status.unmatched_timezones(signup, squad_timezones),
            "squad_count": event.squads.count(),
            "can_manage_list": _can_manage_signup_questions(request.user),
            "note_max_length": NOTE_MAX_LENGTH,
        },
    )
    patch_vary_headers(response, ("HX-Request",))
    return response


def _save(request: HttpRequest, event: Event, signup: EventSignup, *, departed: bool, squads: list[Squad]) -> None:
    """Apply the status dialog's choice and note, and say what happened.

    The choice is a status from the event's list (by primary key), no status, withdrawn, or
    -- for a rider who has left the team, whose status the app sets -- "keep". Bringing a
    withdrawn rider back asks the event's signup requirements, as every other way onto an
    event does. Logfire gets ids and what changed, never the note or a status's name.

    Args:
        request: The request, carrying ``choice`` and ``note``.
        event: The event.
        signup: The signup.
        departed: Whether the rider has left the team's Discord server.
        squads: The squads the rider is a member of.

    """
    name = _name(signup.user)
    choice = (request.POST.get("choice") or "").strip()
    note = (request.POST.get("note") or "").strip()
    if len(note) > NOTE_MAX_LENGTH:
        messages.error(request, f"The note can be at most {NOTE_MAX_LENGTH} characters. Nothing was saved.")
        return

    was_withdrawn = signup.status == EventSignup.Status.WITHDRAWN
    status: SignupStatus | None = None
    if departed:
        if choice not in {KEEP, WITHDRAW}:
            messages.error(request, f"{name} has left the team, so only withdrawing or the note can change.")
            return
    elif choice not in {NO_STATUS, WITHDRAW}:
        status = SignupStatus.objects.filter(event=event, pk=choice).first() if choice.isdigit() else None
        if status is None or (status.retired and status.pk != signup.staff_status_id):
            messages.error(request, "That status isn't on this event's list any more. Nothing was saved.")
            return

    withdrawing = choice == WITHDRAW and not was_withdrawn
    returning = was_withdrawn and choice not in {WITHDRAW, KEEP}
    if returning:
        blockers = signup_blockers(event, signup.user)
        if blockers:
            logfire.info(
                "Signup return refused by signup requirements",
                event_id=event.pk,
                signup_id=signup.pk,
                signup_user_id=signup.user_id,
                admin_user_id=request.user.id,
                blockers=blockers,
            )
            messages.error(
                request,
                f"{name} can't come back on the event yet: it requires {requirement_phrase(blockers)}.",
            )
            return

    note_changed = note != signup.status_note
    status_changed = withdrawing or returning or (choice != KEEP and status != signup.staff_status)
    if not note_changed and not status_changed:
        messages.info(request, "Nothing changed.")
        return

    freed = 0
    with transaction.atomic():
        if withdrawing:
            freed = signup_status.withdraw(signup)
        elif choice not in {KEEP, WITHDRAW}:
            # A status from the list, or none -- which also brings a withdrawn rider back.
            signup.status = EventSignup.Status.REGISTERED
            signup.staff_status = status
        signup.status_note = note
        signup.status_set_by = request.user
        signup.status_set_at = timezone.now()
        signup.save(
            update_fields=["status", "staff_status", "status_note", "status_set_by", "status_set_at", "updated_at"]
        )

    logfire.info(
        "Signup status set",
        event_id=event.pk,
        signup_id=signup.pk,
        signup_user_id=signup.user_id,
        admin_user_id=request.user.id,
        staff_status_id=signup.staff_status_id,
        kind=status.kind if status else None,
        withdrew=withdrawing,
        returned=returning,
        squad_places_freed=freed,
        note_changed=note_changed,
    )
    if withdrawing:
        places = f" Their place in {', '.join(s.name for s in squads)} is free." if freed else ""
        messages.success(request, f"{name} has been withdrawn.{places}")
    elif returning:
        messages.success(request, f"{name} is back on the event.")
    elif status_changed:
        messages.success(request, f"{name}: {status.name if status else 'no status'}.")
    else:
        messages.success(request, "Note saved.")


@login_required
@team_member_required()
@require_http_methods(["GET", "POST"])
def signup_statuses_view(request: HttpRequest, pk: int) -> HttpResponse:
    """List an event's statuses, and add, edit, reorder, retire, delete or copy them.

    Gated like Signup Questions, the other list an event's edit page links to (global event
    admins and superusers). Every change is a small POST naming its ``action``; each comes back
    here with a message. A status in use cannot be deleted, only retired: the signups that have
    it keep it, and it is no longer offered.

    Args:
        request: The HTTP request.
        pk: The event primary key.

    Returns:
        The page, or a redirect after a change or when refused.

    """
    event = get_object_or_404(Event, pk=pk)
    if not _can_manage_signup_questions(request.user):
        logfire.warning("Unauthorized signup statuses access", event_id=pk, user_id=request.user.id)
        messages.error(request, "You don't have permission to manage this event's signup statuses.")
        return redirect("events:event_detail", pk=pk)

    add_form = SignupStatusForm(event=event, prefix="add")
    edit_forms: dict[int, SignupStatusForm] = {}
    if request.method == "POST":
        result = _change_statuses(request, event)
        if result is None:
            return redirect("events:signup_statuses", pk=pk)
        # A form that did not validate: show the page again with it open and its errors.
        if result.instance.pk:
            edit_forms[result.instance.pk] = result
        else:
            add_form = result

    statuses = list(SignupStatus.objects.filter(event=event).annotate(uses=Count("signups")))
    for status in statuses:
        status.form = edit_forms.get(status.pk) or SignupStatusForm(
            instance=status, event=event, prefix=f"status-{status.pk}"
        )
        status.form_open = status.pk in edit_forms
    sources = (
        Event.objects
        .exclude(pk=event.pk)
        .filter(signup_statuses__retired=False)
        .distinct()
        .order_by("-start_date")[:50]
    )
    return render(
        request,
        "events/signup_statuses.html",
        {
            "event": event,
            "waiting": [s for s in statuses if s.is_waiting],
            "set_aside": [s for s in statuses if not s.is_waiting],
            "status_count": len(statuses),
            "at_limit": len(statuses) >= SignupStatus.MAX_PER_EVENT,
            "max_statuses": SignupStatus.MAX_PER_EVENT,
            "add_form": add_form,
            "add_open": add_form.is_bound,
            "sources": sources,
            "kind_choices": SignupStatus.Kind.choices,
            "colour_choices": [
                (value, label, SignupStatus(colour=value).badge_class) for value, label in SignupStatus.Colour.choices
            ],
        },
    )


def _change_statuses(request: HttpRequest, event: Event) -> SignupStatusForm | None:
    """Apply one change from the statuses page.

    Args:
        request: The POST, naming its ``action`` and, for most, the ``status``.
        event: The event.

    Returns:
        A form that did not validate, to show again with its errors; otherwise None, the
        change made (or refused) and a message added.

    """
    action = request.POST.get("action", "")
    log = {"event_id": event.pk, "user_id": request.user.id, "action": action}

    if action == "add":
        if SignupStatus.objects.filter(event=event).count() >= SignupStatus.MAX_PER_EVENT:
            messages.error(request, f"An event can have at most {SignupStatus.MAX_PER_EVENT} statuses.")
            return None
        form = SignupStatusForm(request.POST, event=event, prefix="add")
        if not form.is_valid():
            return form
        status = form.save(commit=False)
        status.event = event
        status.order = SignupStatus.objects.filter(event=event).count()
        status.save()
        logfire.info("Signup status added", **log, status_id=status.pk, kind=status.kind)
        messages.success(request, f"Added {status.name}.")
        return None

    if action == "copy":
        source_pk = request.POST.get("source", "")
        source = Event.objects.filter(pk=source_pk).exclude(pk=event.pk).first() if source_pk.isdigit() else None
        if source is None:
            messages.error(request, "Choose an event to copy from.")
            return None
        with transaction.atomic():
            added, skipped = signup_status.copy_statuses(source, event)
        logfire.info("Signup statuses copied", **log, source_event_id=source.pk, added=added, skipped=skipped)
        if added:
            note = f" Skipped {skipped} already here or past the limit." if skipped else ""
            messages.success(request, f"Copied {added} status{'' if added == 1 else 'es'}.{note}")
        else:
            messages.info(request, "Nothing to copy: this event already has every one of those statuses.")
        return None

    status_pk = request.POST.get("status", "")
    status = SignupStatus.objects.filter(event=event, pk=status_pk).first() if status_pk.isdigit() else None
    if status is None:
        messages.error(request, "That status isn't on this event's list.")
        return None
    log["status_id"] = status.pk

    if action == "edit":
        form = SignupStatusForm(request.POST, instance=status, event=event, prefix=f"status-{status.pk}")
        if not form.is_valid():
            return form
        form.save()
        logfire.info("Signup status updated", **log, kind=status.kind)
        messages.success(request, f"Saved {status.name}.")
    elif action in {"up", "down"}:
        _move(event, status, up=action == "up")
        logfire.info("Signup status moved", **log)
    elif action in {"retire", "restore"}:
        status.retired = action == "retire"
        status.save(update_fields=["retired", "updated_at"])
        logfire.info("Signup status retired" if status.retired else "Signup status restored", **log)
        messages.success(request, f"{'Retired' if status.retired else 'Restored'} {status.name}.")
    elif action == "delete":
        uses = status.signups.count()
        if uses:
            messages.error(request, f"{status.name} is on {uses} signup{'' if uses == 1 else 's'}: retire it instead.")
            return None
        status.delete()
        signup_status.renumber(event)
        logfire.info("Signup status deleted", **log)
        messages.success(request, f"Deleted {status.name}.")
    else:
        messages.error(request, "That change isn't one this page makes.")
    return None


def _move(event: Event, status: SignupStatus, *, up: bool) -> None:
    """Swap a status with its neighbour among those of the same kind, as the page groups them.

    Args:
        event: The event.
        status: The status to move.
        up: True to move it earlier, False later.

    """
    signup_status.renumber(event)
    same_kind = list(SignupStatus.objects.filter(event=event, kind=status.kind))
    index = next(i for i, s in enumerate(same_kind) if s.pk == status.pk)
    other = index - 1 if up else index + 1
    if not 0 <= other < len(same_kind):
        return
    first, second = same_kind[index], same_kind[other]
    with transaction.atomic():
        SignupStatus.objects.filter(pk=first.pk).update(order=second.order)
        SignupStatus.objects.filter(pk=second.pk).update(order=first.order)
