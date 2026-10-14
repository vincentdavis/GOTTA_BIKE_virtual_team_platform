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
def duration_hms(seconds: float | None) -> str:
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


@register.filter
def discord_schedule(result, plan) -> str:
    """Lay out a plan's pull schedule as a code block to paste into Discord.

    Args:
        result: The plan's computed results.
        plan: The plan.

    Returns:
        The text, fences included (see ``services/schedule.py``).

    """
    from apps.ttt_planner.services.schedule import discord_text

    return discord_text(plan, result)


@register.filter
def wheel_name(position: int) -> str:
    """Name a wheel position: 2nd, 3rd, 4th.

    Args:
        position: The position, 1 being the front.

    Returns:
        The position with its suffix.

    """
    from apps.ttt_planner.services.schedule import ordinal

    return ordinal(position)
