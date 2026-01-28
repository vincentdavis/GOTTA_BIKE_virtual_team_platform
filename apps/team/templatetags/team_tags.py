"""Template tags for team app."""

from django import template

register = template.Library()

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
