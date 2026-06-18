"""Squad picker + roster helpers for adding our-team riders from event squads.

Active events are visible events that haven't ended. A squad's roster for this
purpose is the union of its MEMBER ``SquadMember`` users plus its captains and
vice-captains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.utils import timezone

from apps.events.models import Squad, SquadMember

if TYPE_CHECKING:
    from apps.accounts.models import User


def _active_squads():
    """Return squads belonging to active (visible, not-ended) events.

    Returns:
        A Squad queryset with the event preloaded.

    """
    today = timezone.now().date()
    return Squad.objects.filter(event__visible=True, event__end_date__gte=today).select_related("event")


def squads_for_picker(user: User) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the squad picker's two groups for active events.

    Args:
        user: The current user.

    Returns:
        A ``(mine, other)`` tuple of ``{"pk", "label"}`` dicts, each sorted
        alphabetically by label. ``mine`` is squads the user belongs to (member,
        captain, or vice-captain); ``other`` is the remaining active squads.

    """
    squads = _active_squads()
    mine_ids: set[int] = set()
    mine_ids.update(
        SquadMember.objects.filter(
            user=user, status=SquadMember.Status.MEMBER, squad__in=squads
        ).values_list("squad_id", flat=True)
    )
    mine_ids.update(squads.filter(captains=user).values_list("id", flat=True))
    mine_ids.update(squads.filter(vice_captains=user).values_list("id", flat=True))

    mine, other = [], []
    for squad in squads:
        entry = {"pk": squad.pk, "label": f"{squad.event.title} — {squad.name}"}
        (mine if squad.pk in mine_ids else other).append(entry)
    mine.sort(key=lambda d: d["label"].lower())
    other.sort(key=lambda d: d["label"].lower())
    return mine, other


def squad_member_users(squad: Squad) -> list[User]:
    """Return a squad's roster: MEMBER users plus captains and vice-captains.

    Args:
        squad: The squad.

    Returns:
        Deduplicated list of users, ordered by name.

    """
    users: dict[int, User] = {}
    for user in squad.captains.all():
        users[user.pk] = user
    for user in squad.vice_captains.all():
        users[user.pk] = user
    for membership in SquadMember.objects.filter(squad=squad, status=SquadMember.Status.MEMBER).select_related("user"):
        users[membership.user.pk] = membership.user
    return sorted(users.values(), key=lambda u: (u.first_name or "", u.last_name or "", u.username))
