"""Views for zwift app.

The ``/user/zauth`` page lets a user connect (or disconnect) their official
Zwift account via the GOTTA_BIKE Zwift API service. Tokens never touch this
platform — the service is the source of truth, queried live through
``apps.zwift.client``. It is the only way a rider's Zwift account gets verified,
so the profile, the verification page and the zauth banner all link here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.zwift import client, profile_fields, verification

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


@login_required
def zauth_view(request: HttpRequest) -> HttpResponse:
    """Show the user's Zwift connection status and connect/disconnect controls.

    Reads the authoritative status from the Zwift API service. A one-off flash
    from the OAuth callback redirect (``?status=connected&zwid=...``) is surfaced
    as a message but the displayed state always comes from the service.

    Args:
        request: The HTTP request.

    Returns:
        The rendered ``zwift/zauth.html`` page.

    """
    # One-off feedback from the service's post-consent redirect back to this page.
    callback_status = request.GET.get("status")
    if callback_status == "connected":
        messages.success(request, "Your Zwift account was connected successfully.")
    elif callback_status == "error":
        messages.error(request, "Zwift connection was cancelled or failed. Please try again.")

    configured = client.is_configured()
    if configured and callback_status:
        # Back from consent: the answer this page exists to show must not be hidden by a read
        # that failed moments before the rider left for Zwift.
        client.forget_status_failure(str(request.user.pk))
    status = client.get_connection_status(str(request.user.pk)) if configured else None
    service_error = configured and status is None

    # Reconcile platform verification from the authoritative status (not the
    # user-controllable ?status= querystring). This is the on-connect fast path;
    # the hourly task catches everyone else. Never revokes on a None status.
    outcome = verification.apply_status(request.user, status)
    if outcome in ("granted", "revoked"):
        logfire.info("zauth verification synced on view", user_id=request.user.pk, outcome=outcome)

    # Same fast path for country/gender: Zwift knows both, and a rider arrives here
    # straight from consent, so there is no reason to make them wait for a sweep.
    # Only blank fields are filled -- a rider's own answer is never overwritten, and a
    # disagreement is flagged on the profile card instead.
    if status and status.get("connected"):
        filled = profile_fields.fill_missing(
            request.user, client.get_racing_profile(str(request.user.pk))
        )
        if filled:
            messages.info(
                request,
                f"We filled in your {' and '.join(filled)} from Zwift. "
                "You can change it on your profile.",
            )

    context = {
        "configured": configured,
        "service_error": service_error,
        "connected": bool(status and status.get("connected")),
        "zwid": status.get("zwid") if status else None,
        "connected_at": status.get("connected_at") if status else None,
    }
    return render(request, "zwift/zauth.html", context)


@login_required
@require_POST
def zauth_connect(request: HttpRequest) -> HttpResponse:
    """Start the Zwift OAuth connect flow and redirect the browser to consent.

    Requests a consent URL from the service (with this page as the return URL)
    and 302s the user to Zwift. On any failure, returns to the status page with
    an error message rather than leaving the user on a dead end.

    Args:
        request: The HTTP request.

    Returns:
        A redirect to the Zwift consent URL, or back to the status page on error.

    """
    return_url = request.build_absolute_uri(reverse("zwift:zauth"))
    authorize_url = client.get_authorize_url(str(request.user.pk), return_url)
    if not authorize_url:
        logfire.error("Could not start Zwift connect", user_id=request.user.pk)
        messages.error(request, "Could not start the Zwift connection right now. Please try again later.")
        return redirect("zwift:zauth")
    return redirect(authorize_url)


@login_required
@require_POST
def zauth_disconnect(request: HttpRequest) -> HttpResponse:
    """Disconnect the user's Zwift account link and return to the status page.

    Only a call the service answered is reported as done. When the service could not be
    asked, the rider is told so and nothing here changes: the link may still be there, and
    the hourly reconcile would keep verifying them by it.

    Args:
        request: The HTTP request.

    Returns:
        A redirect back to the status page with a result message.

    """
    outcome = client.disconnect_link(str(request.user.pk))
    if outcome is client.DisconnectOutcome.REMOVED:
        messages.success(request, "Your Zwift account was disconnected.")
    elif outcome is client.DisconnectOutcome.NO_LINK:
        messages.info(request, "No connected Zwift account was found to disconnect.")
    elif outcome is client.DisconnectOutcome.FAILED:
        messages.error(request, "We couldn't reach Zwift to disconnect your account. Please try again later.")
    else:
        messages.error(request, "Zwift Link isn't configured right now, so nothing was disconnected.")

    if outcome in (client.DisconnectOutcome.REMOVED, client.DisconnectOutcome.NO_LINK):
        # The service has just said there is no link, which is a real status. Applying it here
        # means the verification goes now, even if the status read on the next page fails.
        verification.apply_status(request.user, {"connected": False})
    logfire.info("Rider disconnected Zwift from the connection page", user_id=request.user.pk, outcome=str(outcome))
    return redirect("zwift:zauth")
