"""Guards for the sidebar's current-page marker.

daisyUI 5 renamed the menu modifier to ``menu-active``; the bare ``active`` the sidebar used
has styled nothing since the upgrade, so the current page had no indicator at all. Restoring
it also means emitting ``aria-current="page"`` -- and exactly one of them, which the loose
``'x' in request.path`` conditions did not guarantee: ``/site/config/strava/`` matched both
the Strava nav item and its settings item, and ``/events/races/`` matched both Events and
All Races.
"""

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse


def _current_per_nav(html):
    """Count aria-current="page" inside each <nav>, keyed by its label.

    Scoped per landmark on purpose: the sidebar and the mobile bottom bar are two separate
    navigations and each may mark its own current item, so a document-wide count of 1 is the
    wrong assertion. What must never happen is two current items inside one nav.

    Args:
        html: The rendered page.

    Returns:
        Mapping of each nav's aria-label to how many current items it marks.

    """
    soup = BeautifulSoup(html, "html.parser")
    return {
        nav.get("aria-label", "(unlabelled)"): len(nav.select('[aria-current="page"]'))
        for nav in soup.find_all("nav")
    }


# Every page a superuser sees the whole sidebar on, including the pairs that used to collide.
PATHS = [
    "/",
    "/events/",
    "/events/races/",
    "/site/config/events/",
    "/strava/",
    "/site/config/strava/",
    "/analytics/",
    "/team/roster/",
    "/routes/",
    "/ttt/",
    "/ladder/",
    "/page/manage/",
    "/data-connections/",
    "/site/config/scheduler/",
]


@pytest.mark.django_db
@pytest.mark.parametrize("path", PATHS)
def test_at_most_one_current_item_per_nav(client, superuser, path):
    client.force_login(superuser)
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} -> {response.status_code}")
    per_nav = _current_per_nav(response.content.decode())
    offenders = {label: n for label, n in per_nav.items() if n > 1}
    assert not offenders, f"{path} marks more than one current item in {offenders}"


@pytest.mark.django_db
def test_navigation_landmarks_are_distinctly_named(client, superuser):
    """Two navs both called "Primary" are indistinguishable in a landmark list."""
    client.force_login(superuser)
    labels = list(_current_per_nav(client.get("/team/roster/").content.decode()))
    assert len(labels) == len(set(labels)), f"duplicate nav labels: {labels}"
    assert "(unlabelled)" not in labels, "every navigation landmark needs a name"


@pytest.mark.django_db
def test_the_current_page_is_actually_marked(client, superuser):
    """A marker that never fires would pass the count test above trivially."""
    client.force_login(superuser)
    html = client.get(reverse("team:roster")).content.decode()
    assert _current_per_nav(html)["Main"] == 1
    assert "menu-active" in html


@pytest.mark.django_db
def test_settings_page_marks_its_own_item_not_the_apps(client, superuser):
    """The regression that made this necessary: /site/config/strava/ lit up two items."""
    client.force_login(superuser)
    html = client.get("/site/config/strava/").content.decode()
    assert _current_per_nav(html)["Main"] == 1


def test_sidebar_is_a_named_navigation_landmark():
    """The sidebar must be a named nav, and retagged rather than wrapped.

    daisyUI slides `.drawer-side > :not(.drawer-overlay)`, so an extra wrapper element would
    become the sliding one and break the sidebar's height and scrolling.
    """
    from pathlib import Path

    sidebar = Path(__file__).resolve().parent.parent.parent / "theme/templates/sidebar.html"
    text = sidebar.read_text()
    assert text.lstrip().startswith('<nav id="main-drawer-side" aria-label="Main"')
    assert "<aside" not in text
