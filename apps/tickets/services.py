"""Service helpers for the tickets app."""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.urls import reverse

from apps.tickets.models import Ticket

if TYPE_CHECKING:
    from apps.accounts.models import GuildMember


def create_member_left_ticket(guild_member: GuildMember) -> Ticket | None:
    """Generate a ticket recording that a Discord guild member left.

    Skips creation if a non-closed ticket already exists for this guild member —
    that way the periodic sync doesn't accumulate duplicate tickets while the
    departure remains in the queue. Once the existing ticket is closed, a
    subsequent departure can create a fresh one.

    Args:
        guild_member: The ``GuildMember`` record whose ``date_left`` was just set.

    Returns:
        The created ``Ticket`` instance, or ``None`` if a current ticket exists.

    """
    if Ticket.objects.filter(
        guild_member=guild_member,
        status__in=[Ticket.Status.NEW, Ticket.Status.IN_PROGRESS],
    ).exists():
        logfire.debug(
            "Skipping member-left ticket; an open ticket already exists",
            guild_member_id=guild_member.pk,
            discord_id=guild_member.discord_id,
        )
        return None

    display_name = guild_member.nickname or guild_member.display_name or guild_member.username

    lines: list[str] = ["A Discord guild member left the server.", ""]
    linked_user = guild_member.user
    if linked_user:
        full_name = linked_user.get_full_name() or display_name
        profile_url = reverse("accounts:public_profile", args=[linked_user.pk])
        lines.append(f"- **Registered user:** [{full_name}]({profile_url})")
    else:
        lines.append("- **Registered user:** _(no linked account)_")
    lines.append(f"- **Discord handle:** `{guild_member.username}`")
    lines.append(f"- **Display name:** {display_name}")
    lines.append(f"- **Discord ID:** `{guild_member.discord_id}`")
    if guild_member.joined_at:
        lines.append(f"- **Joined:** {guild_member.joined_at.strftime('%Y-%m-%d')}")
    if guild_member.date_left:
        lines.append(f"- **Left:** {guild_member.date_left.strftime('%Y-%m-%d %H:%M UTC')}")
    if guild_member.roles:
        role_ids = ", ".join(str(r) for r in guild_member.roles)
        lines.append(f"- **Last known role IDs:** {role_ids}")

    ticket = Ticket.objects.create(
        title=f"Member left guild: {display_name}",
        details="\n".join(lines),
        status=Ticket.Status.NEW,
        category=Ticket.Category.MEMBERSHIP,
        priority=Ticket.Priority.LOW,
        guild_member=guild_member,
    )
    logfire.info(
        "Member-left ticket created",
        ticket_id=ticket.pk,
        guild_member_id=guild_member.pk,
        discord_id=guild_member.discord_id,
        had_linked_user=linked_user is not None,
    )
    return ticket
