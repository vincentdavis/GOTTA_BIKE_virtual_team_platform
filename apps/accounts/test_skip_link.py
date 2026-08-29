"""Guards for the skip-to-content link.

Two halves have to stay together, and one of them lives outside the templates: the link is
hidden with Tailwind's ``sr-only`` and revealed with ``focus:not-sr-only``, and those
utilities are only emitted into the compiled bundle if a template actually uses them. The
bundle is a tracked artifact, so a template edit without a ``manage.py tailwind build`` would
ship a "Skip to content" button permanently visible at the top of every page.
"""

from pathlib import Path

import pytest
from django.urls import reverse

_ROOT = Path(__file__).resolve().parent.parent.parent
_BASE = _ROOT / "theme/templates/base.html"
_BUNDLE = _ROOT / "theme/static/css/dist/styles.css"


def test_skip_link_is_the_first_thing_in_the_body():
    """It has to come before the banners, header and drawer or it skips nothing."""
    base = _BASE.read_text()
    body = base.index("<body")
    link = base.index('href="#main-content"')
    header = base.index("<header")
    assert body < link < header


def test_main_can_receive_focus():
    """<main> needs tabindex="-1" to be a focus target.

    Without it the browser scrolls but leaves focus behind, so the next Tab resumes at the
    top of the page and the link achieves nothing.
    """
    assert '<main id="main-content" tabindex="-1"' in _BASE.read_text()


def test_the_hiding_utilities_are_in_the_compiled_bundle():
    """The failure this catches is a forgotten `manage.py tailwind build`.

    Without these the link is not hidden -- it renders as a visible button on every page.
    """
    bundle = _BUNDLE.read_text()
    assert ".sr-only" in bundle, "run: uv run python manage.py tailwind build"
    assert "not-sr-only" in bundle, "run: uv run python manage.py tailwind build"


@pytest.mark.django_db
def test_skip_link_renders_on_a_real_page(auth_client):
    html = auth_client.get(reverse("team:roster")).content.decode()
    assert html.count('href="#main-content"') == 1
    assert 'id="main-content"' in html
