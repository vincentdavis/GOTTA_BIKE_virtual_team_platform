"""Template filters for the TTT planner."""

import json

from django import template

register = template.Library()


@register.filter
def pretty_json(value) -> str:
    """Render a value as indented JSON for display.

    Args:
        value: Any JSON-serializable value (e.g. a stored request/response dict).

    Returns:
        Indented JSON string, or an empty string for None.

    """
    if value is None:
        return ""
    try:
        return json.dumps(value, indent=2, sort_keys=False)
    except TypeError, ValueError:
        return str(value)


@register.filter
def duration_hms(seconds: float | int | None) -> str:
    """Format a number of seconds as ``H:MM:SS`` or ``M:SS``.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted duration string, or an em dash for falsy/invalid input.

    """
    try:
        total = round(float(seconds))
    except TypeError, ValueError:
        return "—"
    if total <= 0:
        return "—"

    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
