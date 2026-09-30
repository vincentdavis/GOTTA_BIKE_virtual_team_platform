"""The one rule for putting an admin-set address into a link."""


def http_url(value: object) -> str:
    """Return the value if it is an http(s) address, otherwise "".

    Constance holds several addresses an admin types in -- ``DISCORD_URL`` and the
    ``SOCIAL_*_URL`` links -- and templates put them straight into ``href``. Auto-escaping
    stops a value breaking out of the attribute, but not a ``javascript:`` or ``data:``
    address, which is a perfectly well-formed ``href`` that runs script when clicked. The
    footer is on every page, so one bad value would reach every visitor.

    Surrounding whitespace is stripped and the scheme compared case-insensitively, as a
    browser does. Anything that does not then start with ``http://`` or ``https://`` is
    refused -- including a relative path or ``#``, which no setting here should hold.

    Args:
        value: The address as stored.

    Returns:
        The stripped address, or "" when it is not an http(s) one.

    """
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""
