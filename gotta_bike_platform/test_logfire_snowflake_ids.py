"""A Discord id must reach Logfire as a string, never as a number.

Logfire stores a numeric attribute as a JSON number, and a JSON number is a double:
everything past 2**53 comes back rounded. A snowflake is 19 digits, so an id logged as
an int is shown with its last few digits replaced by zeros -- a guild, channel, role or
thread id that matches nothing, on exactly the lines somebody reads when Discord is
misbehaving. Nothing fails at runtime; the log just quietly lies.

So every logfire attribute named after a snowflake has to carry something the scan can
see is a string: a literal, an f-string, ``str(...)``, a ``*_str`` variable, or
``gotta_bike_platform.log_utils.log_id``. The sites that hold a string for a reason the
scan cannot see are listed in ALLOWED, each with that reason.
"""

import ast
from pathlib import Path

from gotta_bike_platform.log_utils import log_id

_ROOT = Path(__file__).resolve().parent.parent

# Attribute names that hold a Discord snowflake, alone or after a prefix
# (required_guild_id, target_channel_id, ...).
SNOWFLAKE_NAMES = ("guild_id", "channel_id", "role_id", "thread_id", "message_id")

# The helper that exists for this, plus the two ways it can be referenced.
HELPER = "log_id"

# Sites the scan cannot prove, holding a string at runtime for the reason given.
# Keyed by file, attribute name and the expression as written, so the entries survive
# the lines moving.
ALLOWED: dict[tuple[str, str, str], str] = {
    (
        "apps/accounts/discord_service.py",
        "role_id",
        "role_id",
    ): "add_discord_role / remove_discord_role take role_id: str, and every caller passes a *_str",
    (
        "apps/dbot_api/api.py",
        "guild_id",
        "guild_id",
    ): "the X-Guild-Id request header, which is a string",
    (
        "apps/dbot_api/api.py",
        "provided_guild_id",
        "guild_id",
    ): "the X-Guild-Id request header, which is a string",
    (
        "apps/events/views.py",
        "thread_id",
        "thread_id",
    ): "_extract_thread_id returns the digits of a thread URL as a string",
    (
        "apps/accounts/tasks.py",
        "channel_id",
        "channel_id",
    ): "a YouTube channel id (UC...), not a Discord snowflake",
    (
        "apps/accounts/tasks.py",
        "channel_id",
        "user.youtube_channel_id",
    ): "a YouTube channel id, stored in a CharField",
    (
        "apps/accounts/utils.py",
        "channel_id",
        "channel_id",
    ): "a YouTube channel id parsed out of a URL, always a string",
    (
        "apps/accounts/utils.py",
        "channel_id",
        "channel_match.group(1)",
    ): "a regex group, always a string",
    (
        "apps/accounts/utils.py",
        "channel_id",
        "external_match.group(1)",
    ): "a regex group, always a string",
}


def _source_files() -> list[Path]:
    """Every project Python file, skipping dependencies, migrations and build output.

    Returns:
        The file paths.

    """
    skip = ("node_modules", "staticfiles", "site-packages", "migrations")
    found = []
    for root in (_ROOT / "apps", _ROOT / "gotta_bike_platform"):
        for path in root.rglob("*.py"):
            parts = path.relative_to(_ROOT).parts
            if any(part in skip or part.startswith(".") for part in parts):
                continue
            found.append(path)
    return sorted(found)


def _is_snowflake_name(name: str) -> bool:
    """Whether an attribute name holds a Discord snowflake.

    Args:
        name: The keyword argument's name.

    Returns:
        True for a snowflake name, bare or prefixed.

    """
    return any(name == base or name.endswith(f"_{base}") for base in SNOWFLAKE_NAMES)


def _is_logfire_call(node: ast.Call) -> bool:
    """Whether a call is made on the logfire module.

    Walks back through attributes and intermediate calls so a chained
    ``logfire.with_tags(...).info(...)`` is recognised too.

    Args:
        node: The call node.

    Returns:
        True if the call's receiver is the logfire module.

    """
    current: ast.expr = node.func
    while True:
        if isinstance(current, ast.Attribute):
            current = current.value
        elif isinstance(current, ast.Call):
            current = current.func
        else:
            return isinstance(current, ast.Name) and current.id == "logfire"


def _is_proven_string(node: ast.expr) -> bool:
    """Whether an expression is visibly a string (or None).

    None is allowed: an id that is not set must stay null in the log rather than
    become the string "None".

    Args:
        node: The value passed to the logfire attribute.

    Returns:
        True if the value cannot reach Logfire as a number.

    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) or node.value is None
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id in {"str", HELPER}
        if isinstance(func, ast.Attribute):
            return func.attr == HELPER
        return False
    if isinstance(node, ast.Name):
        return node.id.endswith("_str")
    if isinstance(node, ast.Attribute):
        return node.attr.endswith("_str")
    if isinstance(node, ast.IfExp):
        return _is_proven_string(node.body) and _is_proven_string(node.orelse)
    return False


def _unproven_sites() -> list[tuple[str, int, str, str]]:
    """Every logfire snowflake attribute the scan cannot show is a string.

    Returns:
        Tuples of relative path, line number, attribute name and the expression.

    """
    sites = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_logfire_call(node):
                continue
            for keyword in node.keywords:
                if not keyword.arg or not _is_snowflake_name(keyword.arg):
                    continue
                if _is_proven_string(keyword.value):
                    continue
                sites.append((relative, keyword.value.lineno, keyword.arg, ast.unparse(keyword.value)))
    return sites


def test_snowflake_ids_are_logged_as_strings():
    offenders = [
        f"{path}:{line} logs {attr}={source} -- wrap it in log_id() "
        f"(gotta_bike_platform.log_utils) or add it to ALLOWED with a reason"
        for path, line, attr, source in _unproven_sites()
        if (path, attr, source) not in ALLOWED
    ]
    assert not offenders, "Discord ids logged as numbers lose their last digits:\n" + "\n".join(offenders)


def test_no_stale_allowances():
    # An entry that no longer matches any call has stopped documenting anything and
    # would silently cover the next site written the same way.
    seen = {(path, attr, source) for path, _, attr, source in _unproven_sites()}
    stale = [
        f"{path} {attr}={source} ({reason})"
        for (path, attr, source), reason in ALLOWED.items()
        if (path, attr, source) not in seen
    ]
    assert not stale, "ALLOWED entries that match nothing any more:\n" + "\n".join(stale)


def test_log_id_keeps_every_digit_of_a_snowflake():
    assert log_id(1317875072089981021) == "1317875072089981021"


def test_log_id_leaves_a_missing_id_null():
    # str(None) is "None", which reads as an id somebody set.
    assert log_id(None) is None


def test_log_id_does_not_touch_strings_or_booleans():
    # A bool is an int in Python, and a setting logged as "True" is not a setting.
    assert log_id("1317875072089981021") == "1317875072089981021"
    assert log_id(True) is True
    assert log_id("") == ""
