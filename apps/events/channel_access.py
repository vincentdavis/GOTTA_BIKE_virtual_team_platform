"""Work out who can actually see a Discord channel.

Channel visibility is computed, not stored. Discord layers guild-level role permissions
under the channel's own overwrites, and a squad channel typically denies ``@everyone`` and
allows one squad role -- but any role or member overwrite can widen that, which is how
people end up seeing squads they are not on.

The algorithm here follows Discord's documented order of precedence:

1. ``@everyone`` guild permissions, OR every role the member holds
2. ADMINISTRATOR short-circuits to "can see everything"
3. the channel's ``@everyone`` overwrite (deny, then allow)
4. the union of the member's role overwrites (all denies, then all allows)
5. the member's own overwrite (deny, then allow)

Category permissions are deliberately not fetched: Discord implements category
inheritance by copying overwrites onto the child channel, so the channel's own
``permission_overwrites`` already carry them.

Everything in this module is pure, so the ordering above can be tested without touching
the Discord API.
"""

from __future__ import annotations

VIEW_CHANNEL = 1 << 10
ADMINISTRATOR = 1 << 3

OVERWRITE_ROLE = 0
OVERWRITE_MEMBER = 1


def _bits(value) -> int:
    """Read a Discord permission bitfield, which arrives as a string.

    Args:
        value: The raw ``permissions`` / ``allow`` / ``deny`` value.

    Returns:
        The value as an int, or 0 if it is missing or unparseable.

    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def effective_permissions(
    *,
    member_role_ids: set[str],
    member_id: str,
    guild_id: str,
    roles_by_id: dict[str, dict],
    overwrites: list[dict],
) -> int:
    """Compute one member's permission bitfield for one channel.

    Args:
        member_role_ids: Discord role ids the member holds.
        member_id: The member's Discord id, for a member-specific overwrite.
        guild_id: The guild id, which doubles as the ``@everyone`` role id.
        roles_by_id: Guild roles keyed by id, each with a ``permissions`` field.
        overwrites: The channel's ``permission_overwrites``.

    Returns:
        The member's effective permission bits in this channel.

    """
    # 1. base: @everyone plus every role the member holds
    perms = _bits((roles_by_id.get(guild_id) or {}).get("permissions"))
    for role_id in member_role_ids:
        perms |= _bits((roles_by_id.get(role_id) or {}).get("permissions"))

    # 2. administrator overrides everything, including channel denies
    if perms & ADMINISTRATOR:
        return ~0

    by_role = {o["id"]: o for o in overwrites if int(o.get("type", OVERWRITE_ROLE)) == OVERWRITE_ROLE}
    by_member = {o["id"]: o for o in overwrites if int(o.get("type", OVERWRITE_ROLE)) == OVERWRITE_MEMBER}

    # 3. the @everyone overwrite
    everyone = by_role.get(guild_id)
    if everyone:
        perms &= ~_bits(everyone.get("deny"))
        perms |= _bits(everyone.get("allow"))

    # 4. role overwrites apply as a union -- all denies first, then all allows, so an
    #    allow on any one of the member's roles beats a deny on another
    allow = deny = 0
    for role_id in member_role_ids:
        overwrite = by_role.get(role_id)
        if overwrite:
            allow |= _bits(overwrite.get("allow"))
            deny |= _bits(overwrite.get("deny"))
    perms &= ~deny
    perms |= allow

    # 5. a member-specific overwrite is the last word
    member = by_member.get(member_id)
    if member:
        perms &= ~_bits(member.get("deny"))
        perms |= _bits(member.get("allow"))

    return perms


def can_view(**kwargs) -> bool:
    """Whether a member can see the channel at all.

    Args:
        **kwargs: As :func:`effective_permissions`.

    Returns:
        True if VIEW_CHANNEL survives the overwrite chain.

    """
    return bool(effective_permissions(**kwargs) & VIEW_CHANNEL)


def describe_overwrites(overwrites: list[dict], role_names: dict[str, str]) -> list[dict]:
    """Summarise a channel's overwrites for display, VIEW_CHANNEL only.

    Args:
        overwrites: The channel's ``permission_overwrites``.
        role_names: Discord role id -> name, for labelling role overwrites.

    Returns:
        One row per overwrite that touches VIEW_CHANNEL, with its effect.

    """
    rows = []
    for overwrite in overwrites:
        allow, deny = _bits(overwrite.get("allow")), _bits(overwrite.get("deny"))
        if not (allow | deny) & VIEW_CHANNEL:
            continue
        is_role = int(overwrite.get("type", OVERWRITE_ROLE)) == OVERWRITE_ROLE
        rows.append({
            "kind": "role" if is_role else "member",
            "id": overwrite["id"],
            "name": role_names.get(overwrite["id"], "") if is_role else "",
            "effect": "allow" if allow & VIEW_CHANNEL else "deny",
        })
    return rows
