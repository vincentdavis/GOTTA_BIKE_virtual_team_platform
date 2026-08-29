"""Guards for the rider hover-card's escape from its scroll container.

daisyUI positions ``.dropdown-content`` absolutely, so the ``overflow-x-auto`` wrapper
around nearly every table on the site clips it -- CSS forces the other axis to ``auto``
too, so those wrappers clip vertically as well, and the card is cut off for the last rows
of any long table. ``shared/_user_tooltip_script.html`` re-points the card at the viewport
on hover. Both halves have to stay wired up, and the script must load exactly once however
many riders are on the page.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

_ROOT = Path(__file__).resolve().parent.parent.parent
# Unique to the script, and absent from the markup -- so a page can be counted without the
# `data-user-tooltip` selector inside the script itself inflating the total.
_SCRIPT_MARKER = "addEventListener('pointerover', enter)"
_MARKUP_MARKER = 'dropdown-bottom" data-user-tooltip'


def test_partial_carries_the_hook():
    partial = (_ROOT / "templates/accounts/_user_tooltip.html").read_text()
    assert _MARKUP_MARKER in partial, "the hover-card wrapper lost its data-user-tooltip hook"


def test_script_is_included_from_base():
    base = (_ROOT / "theme/templates/base.html").read_text()
    assert 'include "shared/_user_tooltip_script.html"' in base


@pytest.mark.django_db
def test_script_loads_once_per_page(auth_client):
    """Included from base.html, not the partial -- which renders once per rider."""
    html = auth_client.get(reverse("team:roster")).content.decode()
    assert html.count(_SCRIPT_MARKER) == 1


@pytest.mark.django_db
def test_rendered_card_carries_the_hook():
    """Rendered, not just grepped -- an {% if %} could put the hook out of reach."""
    from django.template.loader import render_to_string

    html = render_to_string("accounts/_user_tooltip.html", {"display_name": "Miriam Gershenson"})
    assert _MARKUP_MARKER in html
    unhooked = re.findall(r'class="dropdown dropdown-hover dropdown-bottom"(?! data-user-tooltip)', html)
    assert not unhooked, "a hover-card rendered without the hook the script looks for"
