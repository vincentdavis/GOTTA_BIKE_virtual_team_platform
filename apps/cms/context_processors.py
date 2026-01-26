"""Context processors for CMS app."""

from django.db.models import QuerySet
from django.http import HttpRequest

from apps.cms.models import Page


def cms_nav_pages(request: HttpRequest) -> dict[str, QuerySet]:
    """Make CMS nav pages available to all templates.

    Filters pages based on:
    - Published status (drafts excluded)
    - show_in_nav=True
    - User permissions (login, team_member requirements)

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with 'cms_nav_pages' containing filtered QuerySet.

    """
    # Get published pages marked for nav
    pages = Page.objects.filter(
        status=Page.Status.PUBLISHED,
        show_in_nav=True,
    ).order_by("nav_order", "title")

    # Filter based on user permissions
    if not request.user.is_authenticated:
        # Anonymous users: only public pages
        pages = pages.filter(require_login=False, require_team_member=False)
    else:
        # Logged in users: check team member permission
        is_team_member = getattr(request.user, "has_permission", lambda x: False)("team_member")
        if not is_team_member:
            # Non-team-members: exclude team-member-only pages
            pages = pages.filter(require_team_member=False)

    return {"cms_nav_pages": pages}
