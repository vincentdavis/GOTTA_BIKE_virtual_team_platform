"""Context processors for CMS app."""

from django.core.cache import cache
from django.http import HttpRequest

from apps.cms.models import Page

CMS_NAV_CACHE_PREFIX = "cms_nav_pages"
CMS_NAV_CACHE_TIMEOUT = 300  # 5 minutes


def _cache_key(tier: str) -> str:
    """Build cache key for a permission tier.

    Args:
        tier: Permission tier identifier (anon, member, team_member).

    Returns:
        Cache key string.

    """
    return f"{CMS_NAV_CACHE_PREFIX}:{tier}"


def clear_cms_nav_cache():
    """Clear all CMS nav page cache entries."""
    for tier in ("anon", "member", "team_member"):
        cache.delete(_cache_key(tier))


def cms_nav_pages(request: HttpRequest) -> dict[str, list]:
    """Make CMS nav pages available to all templates (cached).

    Filters pages based on:
    - Published status (drafts excluded)
    - show_in_nav=True
    - User permissions (login, team_member requirements)

    Results are cached per permission tier for 5 minutes.

    Splits pages by nav_location into:
    - cms_nav_pages: Pages for the sidebar (main_nav)
    - cms_user_menu_pages: Pages for the user dropdown menu (user_menu)

    Args:
        request: The HTTP request.

    Returns:
        Dictionary with 'cms_nav_pages' and 'cms_user_menu_pages' lists.

    """
    if not request.user.is_authenticated:
        tier = "anon"
    elif getattr(request.user, "has_permission", lambda x: False)("team_member"):
        tier = "team_member"
    else:
        tier = "member"

    key = _cache_key(tier)
    result = cache.get(key)
    if result is not None:
        return result

    pages = Page.objects.filter(
        status=Page.Status.PUBLISHED,
        show_in_nav=True,
    ).order_by("nav_order", "title")

    if tier == "anon":
        pages = pages.filter(require_login=False, require_team_member=False)
    elif tier == "member":
        pages = pages.filter(require_team_member=False)

    result = {
        "cms_nav_pages": list(pages.filter(nav_location=Page.NavLocation.MAIN_NAV)),
        "cms_user_menu_pages": list(pages.filter(nav_location=Page.NavLocation.USER_MENU)),
    }
    cache.set(key, result, CMS_NAV_CACHE_TIMEOUT)
    return result
