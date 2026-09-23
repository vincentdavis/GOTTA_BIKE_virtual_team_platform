"""Helpers shared by logfire call sites."""


def log_id(value: object) -> object:
    """Render an integer id as a string so Logfire keeps every digit.

    Logfire stores a numeric attribute as a JSON number, and a JSON number is a
    double: everything past 2**53 comes back rounded. A Discord snowflake is 19
    digits, so ``required_guild_id=1317875072089981021`` was showing up in
    production as ``791589155654205400`` -- an id that matches no guild, on the
    log line an admin reads to find out why a login was refused.

    Only integers are touched, so a value that is already a string stays one.
    ``None`` passes through as ``None`` rather than becoming the string
    ``"None"``, which would read as an id somebody actually set. Booleans are
    integers in Python, so they are excluded too.

    Args:
        value: The value about to be logged.

    Returns:
        The value as a string if it is an integer, otherwise the value unchanged.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return str(value)
