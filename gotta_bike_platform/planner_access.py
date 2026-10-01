"""Who may change a planner document: a ladder matchup or a TTT plan.

Both planners follow one rule, kept here so they cannot drift apart:

* Any team member may change a document's contents. Every planner view is already
  ``team_member_required``, so that needs no further check in the views.
* Someone who neither created it nor is on its edit squad opens it read-only, and has
  to confirm ("Edit anyway") before the edit controls appear. The confirmation is a
  speed bump against an accidental change, not a lock: it is only ``?edit=1`` on the
  page's address, and the server takes their changes either way.
* Every change records who made it in ``updated_by``, so the creator can see who it was.
* Deleting a document and choosing its edit squad stay with :func:`can_manage`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.events.squads import user_in_squad

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User
    from apps.ladder_planner.models import LadderMatchup
    from apps.ttt_planner.models import TttPlan

# The query parameter the "Edit anyway" confirmation adds to a document's page.
EDIT_PARAM = "edit"


def can_manage(doc: LadderMatchup | TttPlan, user: User) -> bool:
    """Return whether a user may delete a document, choose its edit squad, and edit it unasked.

    Args:
        doc: The ladder matchup or TTT plan.
        user: The requesting user.

    Returns:
        True for its creator, a superuser, or anyone on its edit squad's roster
        (member, captain or vice-captain).

    """
    if user.is_superuser or doc.created_by_id == user.id:
        return True
    return bool(doc.edit_squad_id) and user_in_squad(doc.edit_squad, user)


def edit_requested(request: HttpRequest) -> bool:
    """Return whether the page was opened in edit mode, after the "Edit anyway" confirmation.

    Args:
        request: The request.

    Returns:
        True when the address carries ``?edit=1``.

    """
    return request.GET.get(EDIT_PARAM) == "1"


def record_editor(doc: LadderMatchup | TttPlan, user: User) -> None:
    """Record a user as the document's last editor, which also moves its ``updated_at``.

    Args:
        doc: The ladder matchup or TTT plan being changed.
        user: Who is changing it.

    """
    doc.updated_by = user
    doc.save(update_fields=["updated_by", "updated_at"])
