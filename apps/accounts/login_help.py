"""Tell a rider why their Discord sign-in was refused, on the page they land on.

A refused sign-in used to be a single toast on the login page, and three things were lost:
which Discord account the browser had signed in with (the usual cause is the wrong one), any
second problem (the checks stopped at the first), and -- for an error on Discord's side -- the
real reason, since a Cancel was reported as an unverified email.

``pre_social_login`` now runs the server check and the email check, and on a refusal records
the outcome here, in the refused person's own session, and redirects to ``login_help``. That
page reads it back. Nothing is looked up by Discord id, so the page cannot be used to ask
about anybody else, and a blocked account never gets a diagnosis at all: it is still refused
first, silently, on the login page.

What is stored is what the page shows and no more: the Discord handle and display name, the
last four digits of the id, and the outcome of each check. Never the email address.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.utils import timezone

SESSION_KEY = "discord_login_help"

# Long enough to read the page, follow a fix and come back to it; short enough that a shared
# browser does not keep showing the last person's account name for the rest of the day.
TTL = timedelta(minutes=15)


class GuildStatus(StrEnum):
    """The outcome of the live server-membership check."""

    MEMBER = "member"
    NOT_MEMBER = "not_member"
    # Our side: GUILD_ID is unset, so every sign-in is refused. Not the rider's doing.
    UNCONFIGURED = "unconfigured"
    RATE_LIMITED = "rate_limited"
    # Discord failed, answered with something unreadable, or the login carried no token.
    UNAVAILABLE = "unavailable"


class OAuthProblem(StrEnum):
    """Why Discord's own sign-in step failed, before any check could run."""

    CANCELLED = "cancelled"
    DENIED = "denied"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass(frozen=True)
class GuildCheck:
    """The server check's answer: its status and, when Discord listed them, how many servers."""

    status: GuildStatus
    guild_count: int | None = None


def record_refusal(request, *, extra_data: dict, guild: GuildCheck, email_verified: bool) -> None:
    """Keep what the refused rider needs to see, in their own session.

    Args:
        request: The callback request; its session belongs to the person who just signed in.
        extra_data: The Discord user object allauth fetched.
        guild: The server check's outcome.
        email_verified: Whether Discord has verified the account's email.

    """
    request.session[SESSION_KEY] = {
        "username": extra_data.get("username") or "",
        "display_name": extra_data.get("global_name") or "",
        "id_tail": str(extra_data.get("id") or "")[-4:],
        "guild": str(guild.status),
        "guild_count": guild.guild_count,
        "email_verified": bool(email_verified),
        "at": timezone.now().isoformat(),
    }


def record_oauth_problem(request, problem: OAuthProblem, *, code: str = "") -> None:
    """Keep why Discord's own step failed; no account is known at this point.

    Args:
        request: The callback request.
        problem: What went wrong.
        code: allauth's error code, shown for an unexplained error so an admin can look it up.

    """
    request.session[SESSION_KEY] = {
        "oauth_problem": str(problem),
        "code": code,
        "at": timezone.now().isoformat(),
    }


def read(request) -> dict | None:
    """Return the diagnosis recorded for this session, if there is a recent one.

    A stale one is dropped rather than shown.

    Args:
        request: The request.

    Returns:
        The stored diagnosis, or None.

    """
    stored = request.session.get(SESSION_KEY)
    if not isinstance(stored, dict):
        return None
    try:
        at = datetime.fromisoformat(stored["at"])
    except KeyError, TypeError, ValueError:
        forget(request)
        return None
    if timezone.now() - at > TTL:
        forget(request)
        return None
    return stored


def forget(request) -> None:
    """Drop any stored diagnosis, as a sign-in that passes every check does.

    Args:
        request: The request.

    """
    if SESSION_KEY in request.session:
        del request.session[SESSION_KEY]
