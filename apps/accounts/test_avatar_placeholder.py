"""daisyUI 5 renamed the avatar's ``placeholder`` class to ``avatar-placeholder``.

The old name is inert -- it compiles to nothing -- so the initials circle lost the flex
centring that class gives it and the letters sat at the top of the circle (13px above centre
in the header at the largest text size). Nine templates still used it. Measured in a browser
after the rename: the initials are centred to the pixel in the header, on the profile page and
on a public profile, at every text size.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIRS = [_ROOT / "templates", _ROOT / "theme/templates", *sorted((_ROOT / "apps").glob("*/templates"))]

_WORD = r"(?<![\w-]){}(?![\w-])"


def _avatars_with_the_old_class() -> list[str]:
    """Find class attributes naming both the avatar and daisyUI 4's bare placeholder class.

    A class built with template tags still counts: ``{% if %}placeholder{% endif %}`` is the
    old name too.

    Returns:
        One ``path:line`` entry per offending attribute.

    """
    found = []
    for directory in _TEMPLATE_DIRS:
        for path in sorted(directory.rglob("*.html")):
            text = path.read_text()
            for match in re.finditer(r'class="([^"]*)"', text):
                value = match.group(1)
                if re.search(_WORD.format("avatar"), value) and re.search(_WORD.format("placeholder"), value):
                    line = text.count("\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(_ROOT)}:{line}")
    return found


def test_the_scan_sees_the_templates():
    """An empty scan would pass for the wrong reason."""
    assert len([p for d in _TEMPLATE_DIRS for p in d.rglob("*.html")]) > 100


def test_no_avatar_uses_daisyui_4s_placeholder_class():
    assert _avatars_with_the_old_class() == []


@pytest.mark.django_db
def test_the_header_initials_use_the_daisyui_5_class(auth_client):
    """The fixture's member has no Discord avatar, so the header shows their initials."""
    html = auth_client.get(reverse("team:roster")).content.decode()
    header = html[html.index("<header") : html.index("</header>")]

    assert 'class="btn btn-ghost btn-circle avatar avatar-placeholder"' in header
