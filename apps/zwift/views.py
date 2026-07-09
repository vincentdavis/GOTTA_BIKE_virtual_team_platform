"""Views for zwift app.

The ``/user/zauth`` page lets a user connect (or disconnect) their official
Zwift account via the GOTTA_BIKE Zwift API service. Tokens never touch this
platform — the service is the source of truth, queried live through
``apps.zwift.client``. Not linked from any menu yet (pending official Zwift API
credentials to test with).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.zwift import client

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
    status = client.get_connection_status(str(request.user.pk)) if configured else None
    service_error = configured and status is None

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

    Args:
        request: The HTTP request.

    Returns:
        A redirect back to the status page with a result message.

    """
    if client.disconnect(str(request.user.pk)):
        messages.success(request, "Your Zwift account was disconnected.")
    else:
        messages.info(request, "No connected Zwift account was found to disconnect.")
    return redirect("zwift:zauth")
