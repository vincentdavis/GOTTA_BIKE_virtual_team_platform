"""Service helpers for the tickets app."""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.urls import reverse
from django.utils import timezone

from apps.tickets.models import Ticket

if TYPE_CHECKING:
    from apps.accounts.models import GuildMember, User


def _member_cleanup_lines(user: User) -> list[str]:
    """Build a checklist of squad/event associations an admin should clean up.

    Discord already strips a departed member's roles automatically, so this is
    about the app's own stale records (squad membership, leadership, signups).

    Args:
        user: The linked user whose associations to enumerate.

    Returns:
        Markdown bullet lines, or an empty list if there is nothing to clean up.

    """
    lines: list[str] = []
    lines.extend(
        f"- Squad member: {sm.squad.event.title} / {sm.squad.name} ({sm.get_status_display()})"
        for sm in user.squad_memberships.select_related("squad", "squad__event").all()
    )
    lines.extend(
        f"- Captain of: {squad.event.title} / {squad.name}"
        for squad in user.captain_squads.select_related("event").all()
    )
    lines.extend(
        f"- Vice-captain of: {squad.event.title} / {squad.name}"
        for squad in user.vice_captain_squads.select_related("event").all()
    )
    lines.extend(f"- Event signup: {signup.event.title}" for signup in user.event_signups.select_related("event").all())
    return lines


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
    # A row the bot's leave report created from an empty body has no names at all.
    label = display_name or f"Discord ID {guild_member.discord_id}"

    lines: list[str] = ["A Discord guild member left the server.", ""]
    linked_user = guild_member.user
    # The link can outlive a move to another Discord account: the leave report stamps the old
    # row before any sync has released it (apps.accounts.services._release_user_link). That
    # account signs in with its new id, has not left, and its squads and signups are not
    # this departure's to clean up.
    moved_away = linked_user is not None and (linked_user.discord_id or "") != guild_member.discord_id
    if linked_user:
        full_name = linked_user.get_full_name() or label
        profile_url = reverse("accounts:public_profile", args=[linked_user.pk])
        if moved_away:
            lines.append(
                f"- **Previously linked account:** [{full_name}]({profile_url}), which now uses a "
                "different Discord ID and is not affected by this departure"
            )
        else:
            lines.append(f"- **Registered user:** [{full_name}]({profile_url})")
    else:
        lines.append("- **Registered user:** _(no linked account)_")
    if guild_member.username:
        lines.append(f"- **Discord handle:** `{guild_member.username}`")
    if display_name:
        lines.append(f"- **Display name:** {display_name}")
    lines.append(f"- **Discord ID:** `{guild_member.discord_id}`")
    if guild_member.joined_at:
        lines.append(f"- **Joined:** {guild_member.joined_at.strftime('%Y-%m-%d')}")
    if guild_member.date_left:
        lines.append(f"- **Left:** {guild_member.date_left.strftime('%Y-%m-%d %H:%M UTC')}")
    if guild_member.roles:
        role_ids = ", ".join(str(r) for r in guild_member.roles)
        lines.append(f"- **Last known role IDs:** {role_ids}")

    if linked_user and not moved_away:
        cleanup_lines = _member_cleanup_lines(linked_user)
        if cleanup_lines:
            lines.append("")
            lines.append("**App cleanup needed** (Discord already removed their roles on departure):")
            lines.extend(cleanup_lines)

    ticket = Ticket.objects.create(
        title=f"Member left guild: {label}",
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
        linked_user_moved=moved_away,
    )
    return ticket


# The open refusal ticket is found by this title (plus: Membership, system-generated, about no
# particular member), so there is one per incident rather than one per scheduled run.
SYNC_REFUSAL_TICKET_TITLE = "Guild member sync is holding back sign-outs"

_REFUSAL_REASONS = {
    "empty_member_list": "Discord returned an empty member list",
    "mass_departure": "more than the sync's limit at once",
}


def _sync_refusal_details(
    *,
    departures_refused: str,
    departures_skipped: int,
    unseen_refused: str,
    unseen_skipped: int,
    total_received: int,
    active_before: int,
) -> str:
    """Write the body of the sync refusal ticket.

    Args:
        departures_refused: Why departures were held back, or ``""``.
        departures_skipped: Departures held back.
        unseen_refused: Why never-listed accounts were held back, or ``""``.
        unseen_skipped: Never-listed accounts held back.
        total_received: Distinct members in the list.
        active_before: Active members before the sync.

    Returns:
        Markdown.

    """
    tasks_url = reverse("config_section_page", args=["background_tasks"])
    lines = [
        "The guild member sync saved the members Discord listed, but refused to sign anybody "
        "out, because the list did not look complete. Until this is resolved, nobody who "
        "leaves the Discord server loses their site access or API keys, and every later run is "
        "refused the same way.",
        "",
    ]
    if departures_refused:
        reason = _REFUSAL_REASONS.get(departures_refused, departures_refused)
        lines.append(f"- **Departures held back:** {departures_skipped} ({reason})")
    if unseen_refused:
        reason = _REFUSAL_REASONS.get(unseen_refused, unseen_refused)
        lines.append(f"- **Accounts never seen in the server, held back:** {unseen_skipped} ({reason})")
    lines.append(f"- **Members in Discord's list:** {total_received}")
    lines.append(f"- **Active members before the sync:** {active_before}")
    lines.append(f"- **Last refused run:** {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    if departures_refused == "empty_member_list":
        lines.append(
            "An empty list is never acted on, even when confirmed. Check `DISCORD_BOT_TOKEN`, "
            "`GUILD_ID` and the bot's Server Members intent, then run `sync_guild_members` again "
            f"from [Background Tasks]({tasks_url})."
        )
    else:
        lines.append(
            "**If these numbers are real** (for example, members were pruned in Discord): open "
            f"[Background Tasks]({tasks_url}), tick **Accept a mass departure** on "
            "`sync_guild_members` and run it."
        )
        lines.append("")
        lines.append(
            "**If they are not** (Discord returned a short list): run `sync_guild_members` again "
            "without ticking the box. Each refused run shows as Failed on that page."
        )
    lines.append("")
    lines.append("Close this ticket once a run finishes without being refused.")
    return "\n".join(lines)


def open_sync_refusal_ticket(
    *,
    departures_refused: str,
    departures_skipped: int,
    unseen_refused: str,
    unseen_skipped: int,
    total_received: int,
    active_before: int,
) -> Ticket:
    """Open, or refresh, the one ticket saying the guild sync refused to sign anybody out.

    A refusal otherwise shows only in Logfire, and it repeats on every run until an admin
    confirms the departures -- so nobody who leaves in the meantime is signed out. While a
    ticket is open (new or in progress) each refused run rewrites its details with the
    latest counts instead of filing another; once it is closed, the next refusal opens a
    fresh one.

    Args:
        departures_refused: Why departures were held back, or ``""``.
        departures_skipped: Departures held back.
        unseen_refused: Why never-listed accounts were held back, or ``""``.
        unseen_skipped: Never-listed accounts held back.
        total_received: Distinct members in the list.
        active_before: Active members before the sync.

    Returns:
        The open ticket.

    """
    details = _sync_refusal_details(
        departures_refused=departures_refused,
        departures_skipped=departures_skipped,
        unseen_refused=unseen_refused,
        unseen_skipped=unseen_skipped,
        total_received=total_received,
        active_before=active_before,
    )
    ticket = (
        Ticket.objects
        .filter(
            title=SYNC_REFUSAL_TICKET_TITLE,
            category=Ticket.Category.MEMBERSHIP,
            guild_member__isnull=True,
            submitted_by__isnull=True,
            status__in=[Ticket.Status.NEW, Ticket.Status.IN_PROGRESS],
        )
        .order_by("created_at")
        .first()
    )
    if ticket is not None:
        # Priority and assignee are left as an admin set them; only the facts are refreshed.
        ticket.details = details
        ticket.save()
        logfire.info(
            "Guild sync refusal ticket refreshed",
            ticket_id=ticket.pk,
            departures_skipped=departures_skipped,
            unseen_skipped=unseen_skipped,
        )
        return ticket

    ticket = Ticket.objects.create(
        title=SYNC_REFUSAL_TICKET_TITLE,
        details=details,
        status=Ticket.Status.NEW,
        category=Ticket.Category.MEMBERSHIP,
        # Not low like a single departure: every departure is on hold until someone acts.
        priority=Ticket.Priority.HIGH,
    )
    logfire.info(
        "Guild sync refusal ticket created",
        ticket_id=ticket.pk,
        departures_skipped=departures_skipped,
        unseen_skipped=unseen_skipped,
    )
    return ticket
