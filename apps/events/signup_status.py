"""Where each rider's signup stands: the statuses event staff set, and what the app works out.

Two kinds of answer meet here.

- **What event staff set.** Each event keeps its own list of ``SignupStatus`` rows, empty
  until staff add to it or copy another event's (``copy_statuses``). A status is either
  *waiting for a squad* -- still offered when placing riders, after those who need a squad,
  and cleared when they join one (``clear_waiting``) -- or *set aside*, not offered until
  someone changes it. Withdrawn is built in: it is the signup's own ``status``, and
  ``withdraw`` is the one way to set it.
- **What the app works out.** A rider whose Discord id the guild sync has marked as departed
  has left the team (``departed_user_ids``). Nobody sets that, and it goes away if they
  rejoin. Otherwise a rider is in a squad or needs one.

``bucket`` folds the two into the one answer every page counts and filters by. The pages are
in ``signup_status_views`` (the status dialog and an event's list) and the existing event pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Max
from django.utils import timezone

from apps.accounts.membership import departed_discord_ids
from apps.events.models import EventSignup, SignupStatus, SquadMember

if TYPE_CHECKING:
    from collections.abc import Iterable

    from apps.accounts.models import User
    from apps.events.models import Event

NEEDS = "needs"
WAITING = "waiting"
IN_SQUAD = "in_squad"
SET_ASIDE = "set_aside"
WITHDRAWN = "withdrawn"
LEFT = "left"

#: Every bucket, in the order the filters list them, with the label they show.
BUCKETS = (
    (NEEDS, "Needs a squad"),
    (WAITING, "Waiting for a squad"),
    (IN_SQUAD, "In a squad"),
    (SET_ASIDE, "Set aside"),
    (WITHDRAWN, "Withdrawn"),
    (LEFT, "Left the team"),
)
LABELS = dict(BUCKETS)

#: The riders squads are still filled from -- the rider pickers' default.
TAKING_PART = frozenset({NEEDS, WAITING, IN_SQUAD})


def bucket(signup: EventSignup, status: SignupStatus | None, *, in_squad: bool, departed: bool) -> str:
    """Say where a signup stands, for the counts and filters.

    Withdrawn wins over everything (the rider is not racing whatever else is true), then
    leaving the team, then a set-aside status. A rider in a squad counts as in it even with a
    waiting status, which joining a squad normally clears.

    Args:
        signup: The signup.
        status: Its staff status, already looked up (``signup.staff_status`` would query).
        in_squad: Whether the rider is a member of one of the event's squads.
        departed: Whether the rider has left the team's Discord server.

    Returns:
        One of the bucket keys.

    """
    if signup.status == EventSignup.Status.WITHDRAWN:
        return WITHDRAWN
    if departed:
        return LEFT
    if status is not None and status.kind == SignupStatus.Kind.SET_ASIDE:
        return SET_ASIDE
    if in_squad:
        return IN_SQUAD
    if status is not None:
        return WAITING
    return NEEDS


def bucket_counts(entries: Iterable[dict]) -> list[dict]:
    """Count enriched signups by bucket, for a status filter.

    Args:
        entries: Enriched signups, each carrying a ``bucket`` key.

    Returns:
        ``{"key", "label", "count"}`` for every bucket with at least one rider, in filter order.

    """
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["bucket"]] = counts.get(entry["bucket"], 0) + 1
    return [{"key": key, "label": label, "count": counts[key]} for key, label in BUCKETS if counts.get(key)]


def departed_user_ids(users: Iterable[User]) -> set[int]:
    """Say which of these riders have left the team's Discord server, in one query.

    The same record that signs a departed rider out (``GuildMember.date_left`` for their
    current Discord id), but with no exemption for staff: this reports a fact about the
    person, not a decision about access.

    Args:
        users: The riders.

    Returns:
        The primary keys of those who have left.

    """
    users = [user for user in users if user.discord_id]
    gone = departed_discord_ids(user.discord_id for user in users)
    return {user.pk for user in users if str(user.discord_id) in gone}


def statuses_by_id(event: Event) -> dict[int, SignupStatus]:
    """Load every status on an event's list, retired ones too, in one query.

    Args:
        event: The event.

    Returns:
        The statuses keyed by primary key, in list order.

    """
    return {status.pk: status for status in SignupStatus.objects.filter(event=event)}


def offered_statuses(event: Event, current: SignupStatus | None = None) -> list[SignupStatus]:
    """List the statuses the status dialog offers: the active ones, and a retired current one.

    A retired status stays on the signups that have it, so the dialog must still be able to
    show it as chosen; it is just not offered to anyone else.

    Args:
        event: The event.
        current: The signup's current status, if any.

    Returns:
        The statuses, in list order.

    """
    return [
        status
        for status in SignupStatus.objects.filter(event=event)
        if not status.retired or (current is not None and status.pk == current.pk)
    ]


def withdraw(signup: EventSignup) -> int:
    """Withdraw a signup: out of every squad in its event, the row kept and marked.

    Discord roles are left as they are, as they always have been on withdrawing. Any staff
    status goes, since withdrawn is the status now.

    Args:
        signup: The signup.

    Returns:
        How many squad places were freed.

    """
    removed, _ = SquadMember.objects.filter(squad__event_id=signup.event_id, user_id=signup.user_id).delete()
    signup.status = EventSignup.Status.WITHDRAWN
    signup.staff_status = None
    return removed


def clear_waiting(event: Event, user: User) -> bool:
    """Clear a rider's waiting status once they are in one of the event's squads.

    Called wherever a rider becomes a squad member. Set-aside statuses stay: a rider can be
    set aside and still hold a place. The change is the app's own, so it is stamped without
    anyone's name.

    Args:
        event: The event.
        user: The rider.

    Returns:
        True if a status was cleared.

    """
    return bool(
        EventSignup.objects.filter(event=event, user=user, staff_status__kind=SignupStatus.Kind.WAITING).update(
            staff_status=None, status_set_by=None, status_set_at=timezone.now()
        )
    )


def unmatched_timezones(signup: EventSignup, squad_timezones: set[str]) -> list[str]:
    """Say which timezones a rider picked when none of the event's squads is in any of them.

    Args:
        signup: The signup.
        squad_timezones: The timezones the event's squads are set to.

    Returns:
        The rider's picks, or ``[]`` when a squad matches one of them, the rider picked none
        or no squad has a timezone set.

    """
    picks = list(signup.signup_timezone or [])
    if not picks or not squad_timezones or any(tz in squad_timezones for tz in picks):
        return []
    return picks


def copy_statuses(source: Event, target: Event) -> tuple[int, int]:
    """Copy one event's active statuses onto another's list.

    A status whose name the target already has, in any case, is skipped, as is anything past
    the per-event limit. Copies go to the end of the target's list, in the source's order.

    Args:
        source: The event to copy from.
        target: The event to copy to.

    Returns:
        ``(added, skipped)``.

    """
    existing = list(SignupStatus.objects.filter(event=target))
    have = {status.name.casefold() for status in existing}
    room = SignupStatus.MAX_PER_EVENT - len(existing)
    top = SignupStatus.objects.filter(event=target).aggregate(top=Max("order"))["top"]
    next_order = 0 if top is None else top + 1
    added = skipped = 0
    for status in SignupStatus.objects.filter(event=source, retired=False):
        if status.name.casefold() in have or added >= room:
            skipped += 1
            continue
        SignupStatus.objects.create(
            event=target,
            name=status.name,
            kind=status.kind,
            colour=status.colour,
            rider_message=status.rider_message,
            order=next_order + added,
        )
        have.add(status.name.casefold())
        added += 1
    return added, skipped


def renumber(event: Event) -> None:
    """Make an event's status order run 0..n-1, keeping its current order.

    Args:
        event: The event.

    """
    for index, status in enumerate(SignupStatus.objects.filter(event=event)):
        if status.order != index:
            SignupStatus.objects.filter(pk=status.pk).update(order=index)
