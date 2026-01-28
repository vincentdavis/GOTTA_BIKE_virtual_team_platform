"""Template tags for team app."""

from django import template

from apps.team.models import TeamLink

register = template.Library()

# Map permission keys to display names (from TeamLink.PERMISSION_CHOICES)
PERMISSION_DISPLAY_NAMES: dict[str, str] = dict(TeamLink.PERMISSION_CHOICES)

# Map permission keys to DaisyUI badge colors
PERMISSION_COLORS: dict[str, str] = {
    "PERM_APP_ADMIN_ROLES": "badge-error",
    "PERM_TEAM_CAPTAIN_ROLES": "badge-primary",
    "PERM_VICE_CAPTAIN_ROLES": "badge-secondary",
    "PERM_LINK_ADMIN_ROLES": "badge-accent",
    "PERM_MEMBERSHIP_ADMIN_ROLES": "badge-info",
    "PERM_RACING_ADMIN_ROLES": "badge-success",
    "PERM_TEAM_MEMBER_ROLES": "badge-neutral",
    "PERM_RACE_READY_ROLES": "badge-warning",
    "PERM_APPROVE_VERIFICATION_ROLES": "badge-info",
    "PERM_DATA_CONNECTION_ROLES": "badge-accent",
    "PERM_PAGES_ADMIN_ROLES": "badge-secondary",
}

# Map link types to DaisyUI badge colors
LINK_TYPE_COLORS: dict[str, str] = {
    # Racing series - primary/secondary
    "zrl": "badge-primary",
    "ttt": "badge-secondary",
    "frr": "badge-accent",
    "club_ladder": "badge-info",
    # Action types - success/warning
    "signup": "badge-success",
    "form": "badge-warning",
    "availability": "badge-info",
    # Resources
    "spreadsheet": "badge-neutral",
    "website": "badge-ghost",
    "event": "badge-accent",
    # Zwift platforms
    "zwiftpower": "badge-primary",
    "zwiftracing": "badge-secondary",
    # Default
    "other": "badge-ghost",
}


@register.filter
def link_type_badge_class(link_type: str) -> str:
    """Return the DaisyUI badge class for a link type.

    Args:
        link_type: The link type value (e.g., 'zrl', 'ttt', 'form').

    Returns:
        DaisyUI badge class string (e.g., 'badge-primary').

    """
    return LINK_TYPE_COLORS.get(link_type, "badge-ghost")


@register.filter
def permission_badge_class(permission_key: str) -> str:
    """Return the DaisyUI badge class for a permission key.

    Args:
        permission_key: The permission key (e.g., 'PERM_TEAM_CAPTAIN_ROLES').

    Returns:
        DaisyUI badge class string (e.g., 'badge-primary').

    """
    return PERMISSION_COLORS.get(permission_key, "badge-ghost")


@register.filter
def permission_display_name(permission_key: str) -> str:
    """Return the display name for a permission key.

    Args:
        permission_key: The permission key (e.g., 'PERM_TEAM_CAPTAIN_ROLES').

    Returns:
        Human-readable name (e.g., 'Team Captain').

    """
    return PERMISSION_DISPLAY_NAMES.get(permission_key, permission_key)
