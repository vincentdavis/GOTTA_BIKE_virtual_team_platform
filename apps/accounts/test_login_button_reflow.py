"""Guard for the login page's "Continue with Discord" button at larger text sizes.

daisyUI gives ``.btn`` a fixed height (``var(--size)``, 2.5rem). On a 320px phone the label
wraps -- to two lines at the default size, three at the largest -- and at the largest it
spilled 14px above and below the button: light text on the light card, unreadable (WCAG
1.4.10 Reflow, 1.4.3 Contrast). ``h-auto`` lets the button grow with its label,
``min-h-[var(--size)]`` keeps the usual height for a label on one line, and ``py-1.5`` is
small enough not to add to it (the 24px icon plus padding stays under 2.5rem).

Measured in a browser at 320 and 375px at every text size, in both themes: the label stays
inside the button, and a one-line button is still exactly 40px at the default size.
"""

import re

import pytest
from django.urls import reverse


def _discord_button_classes(html: str) -> set[str]:
    """Read the classes of the link whose label is "Continue with Discord".

    Returns:
        The link's classes.

    """
    match = re.search(r'<a href="[^"]*" class="([^"]*)">\s*<svg[^>]*>.*?</svg>\s*Continue with Discord', html, re.S)
    assert match, "the Discord button moved; find it again before trusting this test"
    return set(match.group(1).split())


@pytest.mark.django_db
def test_the_discord_button_grows_with_its_label(client, discord_app):
    classes = _discord_button_classes(client.get(reverse("account_login")).content.decode())

    assert {"btn", "w-full", "h-auto"} <= classes
    assert "min-h-[var(--size)]" in classes  # a one-line label keeps the usual height


@pytest.mark.django_db
def test_the_button_padding_does_not_change_a_one_line_button(client, discord_app):
    """py-2 made a one-line button 42px: the 24px icon plus 16px padding outgrew 2.5rem."""
    classes = _discord_button_classes(client.get(reverse("account_login")).content.decode())

    assert "py-1.5" in classes
    assert not {"py-2", "py-3", "py-4"} & classes
