"""Template tags and filters for accounts app."""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary: dict, key: str) -> list:
    """Get item from dictionary by key.

    Args:
        dictionary: The dictionary to look up.
        key: The key to retrieve.

    Returns:
        The value for the key, or empty list if not found.

    """
    if dictionary is None:
        return []
    return dictionary.get(key, [])
