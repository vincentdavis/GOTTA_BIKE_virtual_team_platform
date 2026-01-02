"""Views for magic link authentication."""

from django.contrib import messages
from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from apps.magic_links.models import MagicLink


@require_GET
def validate_magic_link(request: HttpRequest, token: str) -> HttpResponse:
    """Validate a magic link token and log the user in.

    Args:
        request: The HTTP request.
        token: The magic link token.

    Returns:
        Redirect to the link's redirect_url on success, or home with error.

    """
    magic_link = get_object_or_404(MagicLink, token=token)

    if not magic_link.is_valid():
        messages.error(request, "This link has expired or already been used.")
        return redirect("/")

    # Consume the link and log in the user
    if magic_link.consume():
        login(request, magic_link.user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, f"Welcome, {magic_link.user.username}!")
        return redirect(magic_link.redirect_url)

    messages.error(request, "Unable to authenticate with this link.")
    return redirect("/")
