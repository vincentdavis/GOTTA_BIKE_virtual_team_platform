"""Helpers shared by every CSV export."""

# Cells beginning with these are executed as formulas by Excel / Sheets when the file
# is opened. Rider-authored text (notes, free-text answers, Discord display names) goes
# straight into exports, so it is prefixed with an apostrophe and rendered inert.
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> object:
    """Neutralise a spreadsheet formula in a text cell.

    Only strings are touched, so a negative rating stays a number rather than
    becoming text a spreadsheet cannot sum.

    Args:
        value: The cell value.

    Returns:
        The value, prefixed with an apostrophe if it would otherwise be evaluated.

    """
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value
