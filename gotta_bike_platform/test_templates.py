"""Repo-wide template guards.

Django's ``{# ... #}`` comment syntax is **single-line only**. A comment spanning two
or more lines is not parsed as a comment at all — it renders verbatim as visible page
text. This has reached the UI more than once (see commits 3e19112 and 3be02f2), and it
is invisible in review because the markup looks like a perfectly ordinary comment, so
it is pinned here instead.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_DIRS = ("templates", "apps", "theme/templates")


def _template_files():
    """Yield every template in the project's template roots.

    Yields:
        Paths to ``.html`` files.

    """
    for rel in _TEMPLATE_DIRS:
        base = _ROOT / rel
        if base.exists():
            yield from base.rglob("*.html")


def _unclosed_comment_lines(path):
    """Yield line numbers in a template where a ``{#`` has no ``#}`` after it.

    Args:
        path: The template to scan.

    Yields:
        1-indexed line numbers of offending comment openers.

    """
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        for match in re.finditer(r"\{#", line):
            if "#}" not in line[match.end() :]:
                yield lineno


def test_no_multiline_django_comments():
    """Every ``{#`` must be closed by ``#}`` on the same line."""
    offenders = [
        f"{path.relative_to(_ROOT)}:{lineno}" for path in _template_files() for lineno in _unclosed_comment_lines(path)
    ]

    assert not offenders, (
        "Multi-line Django comments render as visible page text. "
        "Use a {% comment %} ... {% endcomment %} block instead.\n  " + "\n  ".join(offenders)
    )
