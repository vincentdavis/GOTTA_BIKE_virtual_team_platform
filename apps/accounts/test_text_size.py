"""Guards for the text-size preference.

Scales the whole UI by setting a percentage font-size on :root. That works only because
every size in the stack is rem -- Tailwind's utilities and, crucially, daisyUI's component
sizes, which are written as literal rem values (.table, .menu, .badge, .select and .alert are
all .875rem). Overriding --text-xs / --text-sm instead would reach exactly one rule each:
``var(--text-xs)`` appears once in the whole compiled bundle, on the .text-xs utility itself.

Measured on the roster at the largest step: table cells 14->21px, menu links 14->21,
badges 12->18. The variable-override approach moved only .text-xs, 12->14.
"""

from pathlib import Path

import pytest
from django.urls import reverse

_ROOT = Path(__file__).resolve().parent.parent.parent
_CSS = _ROOT / "theme/static/css/a11y.css"
_BASE = _ROOT / "theme/templates/base.html"
_STEPS = ("large", "larger", "largest")


def test_css_defines_every_step():
    css = _CSS.read_text()
    for step in _STEPS:
        assert f'[data-text-size="{step}"]' in css, f"missing the {step} step"
    # Percentages, not px, so a member who already raised their browser's default keeps it as
    # the baseline and this multiplies it rather than replacing it.
    assert "font-size: 112.5%" in css
    assert "font-size: 150%" in css
    assert "font-size: 24px" not in css


def test_preference_is_applied_before_first_paint():
    """In <head>, or the page renders at the default size and visibly jumps."""
    base = _BASE.read_text()
    head = base[: base.index("</head>")]
    assert 'localStorage.getItem("textSize")' in head
    assert 'setAttribute("data-text-size"' in head


def test_every_step_is_reachable_from_the_control():
    base = _BASE.read_text()
    for step in ("default", *_STEPS):
        assert f'id="textsize-{step}"' in base, f"no button for {step}"
        assert f"setTextSize('{step}')" in base


def test_control_buttons_are_named():
    """The visible label is a bare "A", so each button needs its own accessible name."""
    base = _BASE.read_text()
    for label in ("Default text size", "Large text", "Larger text", "Largest text"):
        assert f'aria-label="{label}"' in base


def test_setter_and_head_script_agree_on_the_storage_key():
    """Two places read this; a typo in either silently breaks persistence."""
    base = _BASE.read_text()
    assert base.count('localStorage.getItem("textSize")') >= 1
    assert 'localStorage.setItem("textSize"' in base


@pytest.mark.django_db
def test_control_renders_for_a_signed_in_member(auth_client):
    html = auth_client.get(reverse("team:roster")).content.decode()
    assert 'id="textsize-largest"' in html
    assert html.count('id="textsize-default"') == 1


@pytest.mark.django_db
def test_a_stored_preference_still_applies_when_signed_out(client):
    """The control lives in the avatar dropdown, so it is only offered to signed-in members.

    The head script is outside that guard on purpose: once a member has chosen a size it must
    keep applying on the login page and anywhere else they are not authenticated, or the
    setting appears to switch itself off.
    """
    html = client.get(reverse("account_login")).content.decode()
    assert 'localStorage.getItem("textSize")' in html
    assert 'id="textsize-largest"' not in html
