"""The event page's tab bar.

The page's sections used to be strung down a long scroll, three of them behind their own
separate collapse toggles, so you had to scroll to discover that "By Category" existed at
all. They are now tabs, which state what the page holds.

Every panel is still rendered and a script hides all but one. That matters for more than
Ctrl+F: each panel's scripts find their elements on load, anything reading the page whole
still sees everything, and with no JS the page degrades to the old stacked layout rather
than showing one section and hiding three.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad

_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE = _ROOT / "templates/events/event_detail.html"
_TABS = ["squads", "signups", "category", "races"]


@pytest.fixture
def event(db) -> Event:
    """Build a visible event with a squad.

    Returns:
        The event.

    """
    from datetime import date, timedelta

    today = date.today()
    ev = Event.objects.create(
        title="Tabbed Event",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
        visible=True,
    )
    Squad.objects.create(event=ev, name="A")
    return ev


@pytest.mark.django_db
def test_every_panel_is_rendered(client, event, superuser):
    """Not one panel per request: the fallback, the scripts and the tests all rely on this."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    for tab in _TABS:
        assert f'data-tab-panel="{tab}"' in body, f"{tab} panel missing"


@pytest.mark.django_db
def test_tabs_are_in_order_and_wired_to_their_panels(client, event, superuser):
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    found = re.findall(r'<a role="tab"[^>]*data-tab="([a-z]+)"', body)
    assert found == _TABS
    for tab in _TABS:
        assert f'aria-controls="panel-{tab}"' in body
        assert f'aria-labelledby="tab-{tab}"' in body


@pytest.mark.django_db
def test_tab_labels_carry_their_counts(client, event, superuser):
    """The bar advertises what is behind each tab -- that is the point of it."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()
    bar = re.search(r'id="event-tabs">(.*?)</div>', body, re.S).group(1)

    assert re.search(r"Squads\s*<span[^>]*>\s*1\s*</span>", bar), "squad count missing from the tab"
    assert "Signups" in bar and "By Category" in bar and "Races" in bar


@pytest.mark.django_db
def test_dialogs_sit_outside_every_panel(client, event, superuser):
    """A <dialog> in a hidden panel cannot be opened, and both are opened from the header."""
    client.force_login(superuser)

    body = client.get(reverse("events:event_detail", args=[event.pk])).content.decode()

    last_panel = body.rindex("data-tab-panel=")
    for dialog_id in ("signup_modal", "add_members_modal"):
        if f'id="{dialog_id}"' in body:
            assert body.index(f'id="{dialog_id}"') > last_panel, f"{dialog_id} is inside a panel"


def test_the_tab_is_the_only_disclosure():
    """The per-section collapses are gone; two disclosures for one section is a trap.

    A section that starts collapsed inside a tab means clicking the tab shows nothing.
    """
    template = _TEMPLATE.read_text()
    assert "toggleSignups" not in template
    assert "toggleByCategory" not in template
    assert 'id="signups-content" style="display:none"' not in template
    assert 'id="category-content" style="display:none"' not in template


def test_panels_are_not_hidden_in_the_markup():
    """Hiding them server-side would break the no-JS fallback."""
    template = _TEMPLATE.read_text()
    assert re.search(r'data-tab-panel="[a-z]+"[^>]*\shidden', template) is None


def test_script_honours_the_tab_query_parameter():
    """So a copied link opens on the panel the sender was looking at."""
    template = _TEMPLATE.read_text()
    assert "URLSearchParams(window.location.search).get('tab')" in template
    assert "searchParams.set('tab'" in template
    # An unknown ?tab= must fall back rather than hide everything.
    assert "indexOf(q) !== -1 ? q : known[0]" in template
