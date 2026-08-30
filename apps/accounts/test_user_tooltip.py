"""Guards for the rider card: how it escapes the table, and how it opens.

Two things bury it. The ``overflow-x-auto`` wrapper around nearly every table clips it
(CSS forces the other axis to ``auto`` too, so those wrappers clip vertically as well).
And ``table-pin-col`` makes every first cell ``position: sticky; z-index: 5`` -- a stacking
context the card lives inside, so the next row's cell paints straight over it. Only the top
layer escapes both. ``position: fixed`` is not enough: it escapes overflow but not stacking
contexts, and that distinction is the whole reason for the popover.

It opens on click, not hover. The card holds four links, and hover-triggered UI containing
controls cannot work: the mouse had to cross a gap to reach it and the card closed on the
way, touch has no hover at all, and it fails WCAG 1.4.13.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

_ROOT = Path(__file__).resolve().parent.parent.parent
# Unique to the script and absent from the markup, so counting it on a page is not inflated
# by the script's own `data-user-tooltip` selector string.
_SCRIPT_MARKER = "var justClosedAt"
_MARKUP_MARKER = 'dropdown-bottom" data-user-tooltip'


def test_partial_carries_the_hook():
    partial = (_ROOT / "templates/accounts/_user_tooltip.html").read_text()
    assert _MARKUP_MARKER in partial, "the hover-card wrapper lost its data-user-tooltip hook"


def test_script_is_included_from_base():
    base = (_ROOT / "theme/templates/base.html").read_text()
    assert 'include "shared/_user_tooltip_script.html"' in base


def test_card_opens_on_click_not_hover():
    """Hover cannot work for a card containing links, so the class must stay off.

    ``dropdown-hover`` is what made daisyUI reveal it on hover; with it gone, nothing shows
    the card except the script, and the pointer can travel to the links.
    """
    partial = (_ROOT / "templates/accounts/_user_tooltip.html").read_text()
    assert "dropdown-hover" not in partial
    script = (_ROOT / "templates/shared/_user_tooltip_script.html").read_text()
    assert "addEventListener('click'" in script
    assert "'pointerover'" not in script


def test_daisyui_open_state_is_set():
    """Without it daisyUI's own rule computes the card to `display: none`.

    `.dropdown:not(details,.dropdown-open,.dropdown-hover:hover,:focus-within)
    .dropdown-content{display:none}` -- with hover gone, only `dropdown-open` keeps it
    rendered. Relying on `:focus-within` instead appears to work, because a click happens to
    focus the trigger, and then breaks whenever it does not.
    """
    script = (_ROOT / "templates/shared/_user_tooltip_script.html").read_text()
    assert "classList.add('dropdown-open')" in script
    assert script.count("classList.remove('dropdown-open')") >= 2  # hide() and the toggle listener


def test_trigger_stays_a_link():
    """So the profile is still reachable if the script never runs, and cmd-click still works."""
    partial = (_ROOT / "templates/accounts/_user_tooltip.html").read_text()
    assert "public_profile" in partial
    assert 'aria-haspopup="dialog"' in partial
    script = (_ROOT / "templates/shared/_user_tooltip_script.html").read_text()
    assert "metaKey" in script and "ctrlKey" in script


def test_card_is_promoted_to_the_top_layer():
    """`position: fixed` escapes overflow but not a stacking context.

    `table-pin-col` puts the card inside a `z-index: 5` sticky cell, and the next row's
    cell then paints over it. Only the top layer gets out of that, so if the popover call
    goes away the card silently goes back to hiding under the rows below it.
    """
    script = (_ROOT / "templates/shared/_user_tooltip_script.html").read_text()
    assert "showPopover()" in script
    assert "hidePopover()" in script
    assert 'setAttribute("popover"' in script or "setAttribute('popover'" in script


def test_popover_attribute_is_not_in_the_markup():
    """The attribute has to be added by script, not shipped in the markup.

    The UA hides `[popover]` until it is shown, so a static attribute would mean the card
    never appears at all without JS.
    """
    partial = (_ROOT / "templates/accounts/_user_tooltip.html").read_text()
    assert "popover" not in partial


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
