"""Sign out riders who have left the team's Discord server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth import logout
from django.urls import reverse
from django_htmx.http import HttpResponseClientRedirect

from apps.accounts.membership import is_departed_member

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse

DEPARTED_MESSAGE = (
    "You have been signed out because you are no longer a member of the team's Discord server. "
    "If you have rejoined, sign in with Discord again."
)


class DepartedMemberLogoutMiddleware:
    """End the session of a rider the guild sync has marked as having left the server.

    A Discord login checks guild membership live, but the session it creates outlives
    the check. This closes that gap on the rider's next request after
    ``GuildMember.date_left`` is stamped -- usually within seconds, by the Discord bot's
    member-left report (``POST /api/dbot/member_left/{discord_id}``), and otherwise by the
    scheduled guild sync, so the lag is at most its interval
    (``SCHEDULER_SYNC_GUILD_MEMBERS_HOURS``). The rule itself, including who is exempt
    (staff, superusers, riders with no ``GuildMember`` row yet), is
    ``apps.accounts.membership.is_departed_member``.

    The request then carries on as anonymous, so a login-protected view redirects to the
    login page and a public one still renders. An HTMX request is the exception: following
    that redirect would swap the login page into a fragment, so it is answered with an
    ``HX-Redirect`` to the login page instead, and the partial view never runs.

    Must sit after ``MessageMiddleware`` (it adds a message) and therefore after
    ``SessionMiddleware`` and ``AuthenticationMiddleware``. Costs one indexed query per
    signed-in request and caches nothing, so there is nothing to invalidate when the sync
    runs.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the chain.

        Args:
            get_response: The next middleware or view.

        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Sign the rider out first if they have left the guild, then continue.

        Args:
            request: The incoming request.

        Returns:
            The response, or an HTMX client redirect to the login page.

        """
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and is_departed_member(user):
            user_id = user.pk
            logout(request)
            messages.warning(request, DEPARTED_MESSAGE)
            is_htmx = request.headers.get("HX-Request") == "true"
            logfire.info(
                "Signed out a rider who has left the Discord server",
                user_id=user_id,
                path=request.path,
                htmx=is_htmx,
            )
            if is_htmx:
                return HttpResponseClientRedirect(reverse("account_login"))
        return self.get_response(request)
