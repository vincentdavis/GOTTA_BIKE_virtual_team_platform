"""Template tags and filters for accounts app."""

from decimal import Decimal

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


@register.filter
def kg_to_lbs(kg: Decimal | float | None) -> str:
    """Convert kg to lbs and format.

    Args:
        kg: Weight in kilograms.

    Returns:
        Weight in pounds as string, or empty string if None.

    """
    if kg is None:
        return ""
    return str(round(float(kg) * 2.20462, 1))


@register.filter
def cm_to_inches(cm: int | None) -> str:
    """Convert cm to inches and format.

    Args:
        cm: Height in centimeters.

    Returns:
        Height in inches as string, or empty string if None.

    """
    if cm is None:
        return ""
    return str(round(float(cm) * 0.393701, 1))


@register.filter
def weight_dual(kg: Decimal | float | None) -> str:
    """Format weight with both kg and lbs.

    Args:
        kg: Weight in kilograms.

    Returns:
        Formatted string like '72.5 kg (159.8 lbs)'.

    """
    if kg is None:
        return "-"
    lbs = round(float(kg) * 2.20462, 1)
    return f"{kg} kg ({lbs} lbs)"


@register.filter
def height_dual(cm: int | None) -> str:
    """Format height with both cm and inches.

    Args:
        cm: Height in centimeters.

    Returns:
        Formatted string like '175 cm (68.9 in)'.

    """
    if cm is None:
        return "-"
    inches = round(float(cm) * 0.393701, 1)
    return f"{cm} cm ({inches} in)"


@register.filter
def weight_diff(record_weight: Decimal | float | None, zp_weight: Decimal | float | None) -> str:
    """Calculate weight difference between record and ZwiftPower.

    Args:
        record_weight: Weight from the verification record (kg).
        zp_weight: Current weight from ZwiftPower (kg).

    Returns:
        Formatted difference string with arrow indicator.

    """
    if record_weight is None or zp_weight is None:
        return ""
    diff = float(record_weight) - float(zp_weight)
    if abs(diff) < 0.05:
        return "no change"
    if diff > 0:
        return f"+{diff:.1f} kg"
    return f"{diff:.1f} kg"
