"""Discord channel-visibility computation.

Channel access is computed, not stored, and the order of precedence is what decides
whether someone can read a squad's channel. These pin that order against Discord's
documented algorithm so the audit cannot quietly start lying.
"""

import pytest

from apps.events.channel_access import ADMINISTRATOR, VIEW_CHANNEL, can_view, describe_overwrites

GUILD = "1"
SQUAD_ROLE = "100"
OTHER_ROLE = "200"
MEMBER = "999"


def _args(*, roles=(), overwrites=(), everyone_perms=VIEW_CHANNEL, role_perms=0, member_id=MEMBER):
    """Assemble arguments for can_view with sensible defaults.

    Returns:
        Keyword arguments ready to splat.

    """
    roles_by_id = {GUILD: {"id": GUILD, "permissions": str(everyone_perms)}}
    for role in (SQUAD_ROLE, OTHER_ROLE):
        roles_by_id[role] = {"id": role, "permissions": str(role_perms)}
    return {
        "member_role_ids": set(roles),
        "member_id": member_id,
        "guild_id": GUILD,
        "roles_by_id": roles_by_id,
        "overwrites": list(overwrites),
    }


def _ow(target, *, allow=0, deny=0, kind=0):
    """Build one permission overwrite.

    Returns:
        The overwrite dict as Discord returns it.

    """
    return {"id": target, "type": kind, "allow": str(allow), "deny": str(deny)}


def test_everyone_can_see_an_unrestricted_channel() -> None:
    """With no overwrites, the guild-level @everyone permission decides it."""
    assert can_view(**_args())


def test_denying_everyone_hides_the_channel() -> None:
    """The usual squad-channel setup starts here."""
    assert not can_view(**_args(overwrites=[_ow(GUILD, deny=VIEW_CHANNEL)]))


def test_the_squad_role_overwrite_lets_its_holders_back_in() -> None:
    """Deny @everyone, allow the squad role -- the intended configuration."""
    args = _args(
        roles=[SQUAD_ROLE],
        overwrites=[_ow(GUILD, deny=VIEW_CHANNEL), _ow(SQUAD_ROLE, allow=VIEW_CHANNEL)],
    )
    assert can_view(**args)


def test_any_other_allowed_role_also_gets_in() -> None:
    """How people end up seeing squads they are not on."""
    args = _args(
        roles=[OTHER_ROLE],
        overwrites=[_ow(GUILD, deny=VIEW_CHANNEL), _ow(OTHER_ROLE, allow=VIEW_CHANNEL)],
    )
    assert can_view(**args)


def test_an_allow_on_one_role_beats_a_deny_on_another() -> None:
    """Role overwrites apply as a union: all denies first, then all allows."""
    args = _args(
        roles=[SQUAD_ROLE, OTHER_ROLE],
        overwrites=[_ow(SQUAD_ROLE, deny=VIEW_CHANNEL), _ow(OTHER_ROLE, allow=VIEW_CHANNEL)],
    )
    assert can_view(**args)


def test_a_member_overwrite_is_the_last_word() -> None:
    """A member-specific allow survives every role-level deny above it."""
    args = _args(
        roles=[SQUAD_ROLE],
        overwrites=[
            _ow(GUILD, deny=VIEW_CHANNEL),
            _ow(SQUAD_ROLE, deny=VIEW_CHANNEL),
            _ow(MEMBER, allow=VIEW_CHANNEL, kind=1),
        ],
    )
    assert can_view(**args)


def test_a_member_deny_beats_a_role_allow() -> None:
    """Same precedence, other direction."""
    args = _args(
        roles=[SQUAD_ROLE],
        overwrites=[_ow(SQUAD_ROLE, allow=VIEW_CHANNEL), _ow(MEMBER, deny=VIEW_CHANNEL, kind=1)],
    )
    assert not can_view(**args)


def test_administrator_ignores_every_deny() -> None:
    """Admins see everything regardless of channel overwrites."""
    args = _args(
        roles=[SQUAD_ROLE],
        role_perms=ADMINISTRATOR,
        overwrites=[_ow(GUILD, deny=VIEW_CHANNEL), _ow(SQUAD_ROLE, deny=VIEW_CHANNEL)],
    )
    assert can_view(**args)


def test_a_member_overwrite_does_not_match_a_role_of_the_same_id() -> None:
    """Overwrite type is what distinguishes them, not the id."""
    args = _args(
        roles=[SQUAD_ROLE],
        overwrites=[_ow(GUILD, deny=VIEW_CHANNEL), _ow(SQUAD_ROLE, allow=VIEW_CHANNEL, kind=1)],
    )
    assert not can_view(**args)


@pytest.mark.parametrize("raw", [None, "", "not-a-number"])
def test_unparseable_permission_bits_are_treated_as_none(raw) -> None:
    """Discord sends these as strings; a malformed one must not blow up the audit."""
    args = _args()
    args["roles_by_id"][GUILD]["permissions"] = raw
    assert not can_view(**args)


def test_overwrites_that_do_not_touch_visibility_are_not_listed() -> None:
    """The summary is about who can see the channel, not every permission bit."""
    rows = describe_overwrites(
        [_ow(SQUAD_ROLE, allow=1 << 11), _ow(OTHER_ROLE, deny=VIEW_CHANNEL)],
        {OTHER_ROLE: "Div 2"},
    )

    assert len(rows) == 1
    assert rows[0]["effect"] == "deny"
    assert rows[0]["name"] == "Div 2"
