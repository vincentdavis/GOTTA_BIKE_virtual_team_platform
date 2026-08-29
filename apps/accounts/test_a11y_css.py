"""Guards for the static accessibility stylesheet.

``theme/static/css/a11y.css`` is plain CSS rather than part of the Tailwind source on
purpose: the compiled bundle is a tracked artifact only regenerated on deploy, so a rule
added to the source would not take effect locally until someone ran ``tailwind build``.
Being loaded after ``{% tailwind_css %}`` is also what lets it win ties against daisyUI.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_CSS = _ROOT / "theme/static/css/a11y.css"
_BASE = _ROOT / "theme/templates/base.html"


def test_stylesheet_is_loaded_after_the_tailwind_bundle():
    """Order is load-bearing: equal-specificity rules are decided by document order."""
    base = _BASE.read_text()
    assert "css/a11y.css" in base
    assert base.index("{% tailwind_css %}") < base.index("css/a11y.css")


def test_focus_ring_is_forced():
    """DaisyUI sets `outline-style: none` on thirteen selectors, the whole sidebar included.

    Those rules reach specificity 0,2,1 and are scattered through the bundle, so the outline
    properties are forced rather than matched one by one. Safe here only because the project
    defines no focus styles of its own -- if that stops being true, revisit this.
    """
    css = _CSS.read_text()
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--color-base-content) !important" in css
    # :focus-visible, not :focus -- mouse users should not get rings on every click.
    # Comments are stripped first: they name daisyUI's own :focus selectors.
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert ":focus" in rules
    assert ":focus:" not in rules
    assert not re.search(r":focus(?!-visible)[\s,{]", rules)


def test_no_template_suppresses_focus():
    """The forced ring is justified by there being nothing of ours to override."""
    offenders = [
        str(p.relative_to(_ROOT))
        for d in ("templates", "apps", "theme/templates")
        for p in (_ROOT / d).rglob("*.html")
        if "outline-none" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, f"outline-none would fight the focus ring: {offenders}"


def test_reduced_motion_keeps_spinners_moving():
    """A frozen spinner reads as a hung page, so they are slowed rather than stopped."""
    css = _CSS.read_text()
    assert "prefers-reduced-motion: reduce" in css
    assert ".loading," in css and ".htmx-indicator" in css
    # Durations go to ~0 rather than `animation: none`, so transitionend still fires.
    assert "animation-duration: 0.01ms !important" in css
