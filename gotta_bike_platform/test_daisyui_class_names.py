"""Retired daisyUI 4 class names must not come back.

daisyUI 5 renamed the tab styles, and the old names style nothing at all -- a bar written
with one renders as plain text, which is how four pages quietly lost their tabs. Nothing in
a build fails on an unknown utility class, so this is the only thing that would notice.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Retired name -> what daisyUI 5 calls it now.
RENAMED: dict[str, str] = {
    "tabs-bordered": "tabs-border",
    "tabs-boxed": "tabs-box",
    "tabs-lifted": "tabs-lift",
}


def _templates() -> list[Path]:
    """Every project template, skipping dependencies and build output.

    Returns:
        The template paths.

    """
    skip = ("node_modules", "staticfiles", "site-packages")
    roots = [_ROOT / "templates", _ROOT / "theme", _ROOT / "apps"]
    found = []
    for root in roots:
        for path in root.rglob("*.html"):
            parts = path.relative_to(_ROOT).parts
            if any(part in skip or part.startswith(".") for part in parts):
                continue
            found.append(path)
    return found


def test_no_retired_daisyui_tab_classes():
    found = []
    for path in _templates():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for old, new in RENAMED.items():
            # Word boundary: tabs-border must not match tabs-bordered's replacement.
            if re.search(rf"\b{old}\b", text):
                found.append(f"{path.relative_to(_ROOT)} uses {old} -- daisyUI 5 calls it {new}")
    assert not found, "retired daisyUI class names:\n" + "\n".join(found)
