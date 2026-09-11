"""Per-event requirements a rider must meet before they can be put on an event.

Two optional flags on ``Event``: ``require_complete_profile_signup`` (on by default) and
``require_race_verified_signup`` (off by default). They apply to every way onto an event, not
only the rider's own signup: a squad invite link, and a captain or admin adding members, are
held to them too -- so none of those can become the way round them. ``signup_blockers`` is the
one rule all of those ask, so they cannot come to disagree about who may join.

A rider already signed up stays signed up when a requirement is turned on, or when they later
stop meeting it; the requirements govern joining, not staying. The Django admin is left
unrestricted, as the staff tool for fixing data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import reverse

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.events.models import Event

# What each blocker needs, and where the rider goes to fix it. Keyed so the event page, the
# squad invite page and the messages all word a blocker the same way.
BLOCKERS: dict[str, dict[str, str]] = {
    "profile": {
        "needs": "a complete profile",
        "missing": "incomplete profile",
        "fix_label": "Complete your profile",
        "fix_url_name": "accounts:profile",
    },
    "race_verified": {
        "needs": "Race Verified status",
        "missing": "not Race Verified",
        "fix_label": "Get Race Verified",
        "fix_url_name": "accounts:verification",
    },
}


def signup_blockers(event: Event, user: User) -> list[str]:
    """Say what stops a rider being put on an event, if anything.

    Race Verified is the cached ``User.is_race_ready``, the same status the roster and the
    Discord race-ready role use; recalculating it here would be queries per rider. There is
    no superuser exception, as with the event's availability requirement.

    Args:
        event: The event.
        user: The rider who would be signed up.

    Returns:
        ``BLOCKERS`` keys, in a fixed order; empty when nothing stops them.

    """
    blockers = []
    if event.require_complete_profile_signup and not user.is_profile_complete:
        blockers.append("profile")
    if event.require_race_verified_signup and not user.is_race_ready:
        blockers.append("race_verified")
    return blockers


def requirement_phrase(blockers: list[str]) -> str:
    """Name what the event requires, for a message: "a complete profile and Race Verified status".

    Args:
        blockers: Keys from ``signup_blockers``.

    Returns:
        The phrase.

    """
    return " and ".join(BLOCKERS[key]["needs"] for key in blockers)


def missing_phrase(blockers: list[str]) -> str:
    """Say briefly what a rider is missing, for a list of names: "incomplete profile, not Race Verified".

    Args:
        blockers: Keys from ``signup_blockers``.

    Returns:
        The phrase.

    """
    return ", ".join(BLOCKERS[key]["missing"] for key in blockers)


def blocker_details(blockers: list[str]) -> list[dict[str, str]]:
    """Describe each blocker with a link to where it is fixed, for a page to render.

    Args:
        blockers: Keys from ``signup_blockers``.

    Returns:
        One dict per blocker with ``key``, ``needs``, ``fix_label`` and ``fix_url``.

    """
    return [
        {
            "key": key,
            "needs": BLOCKERS[key]["needs"],
            "fix_label": BLOCKERS[key]["fix_label"],
            "fix_url": reverse(BLOCKERS[key]["fix_url_name"]),
        }
        for key in blockers
    ]
