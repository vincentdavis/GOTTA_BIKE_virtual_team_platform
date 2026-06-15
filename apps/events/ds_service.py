"""Directeur Sportif (DS) squad-role logic, shared by the DS views and the daily sweep.

A DS is any team member helping with a scheduled race. When added they are given the
squad's Discord role; after the race a daily sweep removes it. We only remove a role we
actually assigned, and never strip a role the user holds for another reason (squad member,
captain/vice-captain, or DS on another still-active race for the same squad).

This module deliberately depends only on models + discord_service (no views), so the
background task can import it without pulling in the view layer.
"""

import logfire

from apps.accounts.discord_service import add_discord_role, remove_discord_role
from apps.events.models import SlotDS, Squad, SquadMember


def assign_squad_role(user, squad: Squad, *, actor_id: int | None = None) -> bool:
    """Give ``user`` the squad's Discord role if they don't already hold it.

    Args:
        user: The DS user.
        squad: The squad whose ``team_discord_role`` is assigned.
        actor_id: Acting admin user id, for logging.

    Returns:
        True only if we actually added the role (the caller records this so the daily sweep
        knows to remove it). False if skipped, already held, or the API call failed.

    """
    role_id = squad.team_discord_role
    if not role_id or not user.discord_id:
        return False
    role_id_str = str(role_id)
    if user.has_discord_role(role_id_str):
        return False  # already held; not ours to remove later
    if not add_discord_role(user.discord_id, role_id_str):
        logfire.error("Failed to assign squad role to DS", user_id=user.id, role_id=role_id_str)
        return False
    roles = user.discord_roles or {}
    roles[role_id_str] = squad.name
    user.discord_roles = roles
    user.save(update_fields=["discord_roles"])
    logfire.info("Assigned squad role to DS", user_id=user.id, role_id=role_id_str, actor_id=actor_id)
    return True


def remove_squad_role(user, squad: Squad, *, actor_id: int | None = None) -> bool:
    """Remove the squad's Discord role from ``user`` and update their role cache.

    Args:
        user: The DS user.
        squad: The squad whose ``team_discord_role`` is removed.
        actor_id: Acting admin user id (or None for the scheduled task), for logging.

    Returns:
        True on success or if already missing, False on API failure, skipped otherwise.

    """
    role_id = squad.team_discord_role
    if not role_id or not user.discord_id:
        return False
    role_id_str = str(role_id)
    if not user.has_discord_role(role_id_str):
        return True
    if not remove_discord_role(user.discord_id, role_id_str):
        logfire.error("Failed to remove squad role from DS", user_id=user.id, role_id=role_id_str)
        return False
    roles = user.discord_roles or {}
    roles.pop(role_id_str, None)
    user.discord_roles = roles
    user.save(update_fields=["discord_roles"])
    logfire.info("Removed squad role from DS", user_id=user.id, role_id=role_id_str, actor_id=actor_id)
    return True


def user_entitled_to_squad_role(user, squad: Squad) -> bool:
    """Whether ``user`` should keep the squad role for a reason other than a DS assignment.

    True if they are a current member of the squad or a captain/vice-captain.

    Args:
        user: The user to check.
        squad: The squad.

    Returns:
        True if the user is entitled to the squad role independent of any DS assignment.

    """
    if squad.squad_members.filter(user=user, status=SquadMember.Status.MEMBER).exists():
        return True
    return squad.captains.filter(pk=user.pk).exists() or squad.vice_captains.filter(pk=user.pk).exists()


def should_remove_squad_role(user, squad: Squad, *, exclude_slot_ds_pk: int | None = None) -> bool:
    """Whether it is safe to strip the squad role from a (former) DS.

    Safe only when the user is not otherwise entitled (member/captain) and has no other
    still-active DS assignment for the same squad (so multiple races don't strip each other).

    Args:
        user: The DS user.
        squad: The squad.
        exclude_slot_ds_pk: A SlotDS pk to ignore (the one being removed/handled).

    Returns:
        True if the squad role can be removed.

    """
    if not squad.team_discord_role:
        return False
    if user_entitled_to_squad_role(user, squad):
        return False
    others = SlotDS.objects.filter(
        user=user,
        selection__grid__squad=squad,
        role_was_assigned=True,
        role_removed_at__isnull=True,
    ).exclude(pk=exclude_slot_ds_pk)
    return not others.exists()
