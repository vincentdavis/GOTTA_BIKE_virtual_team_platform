"""Template tag for the manage-squads cards' Stats section."""

from django import template

from apps.events.squad_stats import SquadStats
from apps.events.squad_stats import squad_stats as build_squad_stats

register = template.Library()


@register.simple_tag
def squad_stats(members: list[dict]) -> SquadStats:
    """Summarise a squad's riders for its Stats table.

    Computed in the template rather than the view so the HTMX re-render of one squad's
    riders (``_squad_manage_panel.html``) refreshes the table from the same members.

    Args:
        members: The squad's enriched member dicts.

    Returns:
        The squad's Stats table.

    """
    return build_squad_stats(members)
