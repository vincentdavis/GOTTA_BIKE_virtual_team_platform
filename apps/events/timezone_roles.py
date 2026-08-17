"""Optional per-event mapping from a signup timezone/region option to a Discord role.

An event's ``timezone_options`` are already region labels ("US EAST", "EMEA West", …)
that riders pick from at signup into ``EventSignup.signup_timezone``. This module turns
that existing choice into Discord roles: ``Event.timezone_role_map`` maps an option label
to a role ID, and a rider is granted the roles for the options they selected.

An empty map means the feature is off, so it is opt-in per event with no extra flag.

Two rules worth keeping
-----------------------
**Labels are the key, and labels are free text.** An admin can rename or delete a
timezone option after riders have signed up. The map is therefore always read through
:func:`mapped_roles`, which drops entries whose label is no longer an option — a stale
key grants nothing rather than raising. Stored ``signup_timezone`` values are never
rewritten, matching how signup-question answers are treated as historical.

**Removal is deliberately narrow.** :func:`roles_to_drop` only ever proposes roles the
rider is losing *within this event's map*, and callers must not strip a role the rider
still holds for another reason. Squad ``region_role`` already has this problem and solves
it with ``_unassign_region_role_if_unused``; a timezone role can easily be the same
Discord role as a squad's region role, so the two must not fight. Withdrawing from an
event does not drop these roles at all, matching the existing convention for
``event_role`` and ``team_discord_role``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.events.models import Event


def mapped_roles(event: Event) -> dict[str, str]:
    """Return the event's usable ``{timezone option: role id}`` pairs.

    Entries are dropped when the label is no longer one of the event's
    ``timezone_options``, or when the role ID is empty/zero — so a stale or half-filled
    map degrades to "no role" instead of granting something unintended.

    Args:
        event: The event whose map to read.

    Returns:
        Mapping of option label to role ID string, in ``timezone_options`` order.

    """
    raw = event.timezone_role_map if isinstance(event.timezone_role_map, dict) else {}
    options = [o for o in (event.timezone_options or []) if isinstance(o, str)]
    resolved: dict[str, str] = {}
    for option in options:
        role_id = str(raw.get(option) or "").strip()
        if role_id and role_id != "0":
            resolved[option] = role_id
    return resolved


def is_enabled(event: Event) -> bool:
    """Report whether this event maps any timezone option to a role.

    Args:
        event: The event to check.

    Returns:
        True when at least one option maps to a role.

    """
    return bool(mapped_roles(event))


def roles_for_selection(event: Event, selected: list | None) -> list[str]:
    """Return the role IDs a rider earns for the options they picked.

    A rider may select several options; each mapped one contributes a role, since
    selecting two regions is a claim to race in both.

    Args:
        event: The event holding the map.
        selected: The rider's ``signup_timezone`` list.

    Returns:
        Role ID strings, deduplicated, in ``timezone_options`` order.

    """
    mapping = mapped_roles(event)
    chosen = {s for s in (selected or []) if isinstance(s, str)}
    # dict.fromkeys dedupes while preserving order: two options may map to one role.
    return list(dict.fromkeys(role_id for option, role_id in mapping.items() if option in chosen))


def roles_to_drop(event: Event, *, before: list | None, after: list | None) -> list[str]:
    """Return role IDs the rider no longer qualifies for after editing their signup.

    Only considers this event's map. A role that is still earned by another option the
    rider kept is never proposed for removal, so overlapping labels pointing at one role
    are safe.

    Callers must still check the rider does not hold the role for an unrelated reason
    (a squad ``region_role``, another event, a manual grant) before removing it.

    Args:
        event: The event holding the map.
        before: The rider's previous ``signup_timezone`` selection.
        after: Their new selection.

    Returns:
        Role ID strings to consider removing.

    """
    kept = set(roles_for_selection(event, after))
    return [role_id for role_id in roles_for_selection(event, before) if role_id not in kept]


def role_columns(event: Event, role_names: dict[str, str] | None = None) -> list[dict]:
    """Describe the timezone-role columns for the manage-roles table.

    Args:
        event: The event holding the map.
        role_names: Optional ``{role_id: name}`` lookup for display.

    Returns:
        One dict per mapped option: ``{"option", "role_id", "name"}``.

    """
    names = role_names or {}
    return [
        {"option": option, "role_id": role_id, "name": names.get(role_id, "")}
        for option, role_id in mapped_roles(event).items()
    ]


def parse_role_map(
    post,
    options: list | None,
    *,
    allowed_role_ids: set[str] | None = None,
    current: dict | None = None,
) -> dict[str, str]:
    """Read the event-edit form's per-option role selects into a map.

    Fields are named ``tz_role_map__<label>``. Only the event's current options are read,
    so a removed option cannot be resurrected by a crafted POST, and blank selections are
    omitted rather than stored as empty strings.

    Role IDs are re-validated server-side against the event's prefix-filtered roles, the
    same defence the squad and coordinator pickers use — the rendered ``<select>`` cannot
    be trusted. A value already saved for that option is grandfathered through, so an
    admin narrowing the event's prefixes does not silently wipe existing mappings.

    Args:
        post: The request POST QueryDict.
        options: The event's timezone options.
        allowed_role_ids: Role IDs the event's prefixes permit. None skips the check.
        current: The event's existing map, whose values are always allowed to survive.

    Returns:
        ``{option: role id}`` for the options that were given a permitted role.

    """
    existing = current if isinstance(current, dict) else {}
    parsed: dict[str, str] = {}
    for option in options or []:
        if not isinstance(option, str):
            continue
        value = str(post.get(f"tz_role_map__{option}", "") or "").strip()
        if not value or value == "0":
            continue
        # Reject a role the prefixes don't permit, unless the admin already had it saved
        # for this option (narrowing prefixes must not silently wipe existing mappings).
        rejected = allowed_role_ids is not None and value not in allowed_role_ids
        if rejected and str(existing.get(option) or "") != value:
            continue
        parsed[option] = value
    return parsed
