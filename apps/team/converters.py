"""Unit conversion utilities for weight and height."""

from decimal import ROUND_HALF_UP, Decimal

# Conversion constants
KG_TO_LBS = Decimal("2.20462")
LBS_TO_KG = Decimal("0.453592")
CM_TO_INCHES = Decimal("0.393701")
INCHES_TO_CM = Decimal("2.54")


def kg_to_lbs(kg: Decimal | float | int) -> Decimal:
    """Convert kilograms to pounds.

    Args:
        kg: Weight in kilograms.

    Returns:
        Weight in pounds, rounded to 1 decimal place.

    """
    return (Decimal(str(kg)) * KG_TO_LBS).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def lbs_to_kg(lbs: Decimal | float | int) -> Decimal:
    """Convert pounds to kilograms.

    Args:
        lbs: Weight in pounds.

    Returns:
        Weight in kilograms, rounded to 2 decimal places.

    """
    return (Decimal(str(lbs)) * LBS_TO_KG).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cm_to_inches(cm: int | float) -> Decimal:
    """Convert centimeters to inches.

    Args:
        cm: Height in centimeters.

    Returns:
        Height in inches, rounded to 1 decimal place.

    """
    return (Decimal(str(cm)) * CM_TO_INCHES).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def inches_to_cm(inches: Decimal | float | int) -> int:
    """Convert inches to centimeters.

    Args:
        inches: Height in inches.

    Returns:
        Height in centimeters, rounded to nearest integer.

    """
    return int((Decimal(str(inches)) * INCHES_TO_CM).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_weight_dual(kg: Decimal | float | None) -> str:
    """Format weight showing both units.

    Args:
        kg: Weight in kilograms.

    Returns:
        Formatted string like '72.5 kg (159.8 lbs)'.

    """
    if kg is None:
        return "-"
    lbs = kg_to_lbs(kg)
    return f"{kg} kg ({lbs} lbs)"


def format_height_dual(cm: int | None) -> str:
    """Format height showing both units.

    Args:
        cm: Height in centimeters.

    Returns:
        Formatted string like '175 cm (68.9 in)'.

    """
    if cm is None:
        return "-"
    inches = cm_to_inches(cm)
    return f"{cm} cm ({inches} in)"
