"""Views for GOTTA_BIKE_virtual_team_platform project."""

from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def home(request):
    """Render the home page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered home page template.

    """
    return render(request, "index.html")


@require_GET
def about(request):
    """Render the about page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered about page template.

    """
    return render(request, "about.html")
