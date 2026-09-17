"""The roster's live search and "Show more riders", and the shared index behind both.

A live search asks for the results region on every pause in typing, and "Show more" asks for
the next page's cards as the reader scrolls. Both are htmx requests to the same view, which
answers with a fragment; a plain request, a history restore, or any other htmx request still
gets the whole page. Each would have waited most of a second for the roster to be rebuilt,
so one built index now serves everyone for a minute, with the reader's own event signups
laid over it fresh.
"""

import re
import threading
import time

import pytest
from django.urls import reverse

from apps.accounts.models import GuildMember
from apps.team import rosterv2
from apps.team import views as team_views

RESULTS = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "roster-results"}
MORE = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "roster-more"}
CARD = 'class="card bg-base-100'


def _get(client, headers=None, **params):
    return client.get(reverse("team:roster"), params, **(headers or {}))


def _page_size(monkeypatch, size):
    monkeypatch.setattr(team_views, "ROSTER_PAGE_SIZE", size)


# --- a live search gets the results region, not the page ------------------------------------


@pytest.mark.django_db
def test_a_live_search_gets_only_the_results(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")
    roster_rider(zwid=1002, name="Bo Climber")

    response = _get(auth_client, RESULTS, q="ada")
    body = response.content.decode()

    assert response.status_code == 200
    assert "<html" not in body
    assert body.lstrip().startswith('<div id="roster-results">')
    assert 'id="roster-results"' in body
    assert "Ada Racer" in body
    assert "Bo Climber" not in body


@pytest.mark.django_db
def test_a_live_search_tells_screen_readers_what_it_found(auth_client, roster_rider):
    """The count is read out from a region outside the swap, updated out of band."""
    roster_rider(zwid=1001, name="Ada Racer")
    roster_rider(zwid=1002, name="Bo Climber")

    found = _get(auth_client, RESULTS, q="ada").content.decode()
    nothing = _get(auth_client, RESULTS, q="nobody").content.decode()

    assert '<p id="roster-announcer" hx-swap-oob="innerHTML">Showing 1 of 2 riders.</p>' in found
    assert '<p id="roster-announcer" hx-swap-oob="innerHTML">No riders match.</p>' in nothing
    assert "No riders match" in nothing.split('id="roster-announcer"')[0]  # and on screen


@pytest.mark.django_db
def test_the_whole_page_has_the_announcer_and_no_out_of_band_copy(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    body = _get(auth_client, q="ada").content.decode()

    assert body.count('id="roster-announcer"') == 1
    assert 'role="status" aria-live="polite"></p>' in body
    assert "hx-swap-oob" not in body


@pytest.mark.django_db
def test_restoring_history_gets_the_whole_page(auth_client, roster_rider):
    """A history restore is swapped in as the whole body; a fragment would blank the page."""
    roster_rider(zwid=1001, name="Ada Racer")

    body = _get(auth_client, {**RESULTS, "HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}, q="ada").content.decode()

    assert "<html" in body
    assert "hx-swap-oob" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["", "main-content", "roster-list"])
def test_any_other_htmx_request_gets_the_whole_page(auth_client, roster_rider, target):
    roster_rider(zwid=1001, name="Ada Racer")

    body = _get(auth_client, {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": target}).content.decode()

    assert "<html" in body


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, RESULTS, MORE])
def test_every_answer_varies_on_the_htmx_header(auth_client, roster_rider, headers):
    """One URL, a page or a fragment: a cache that ignored the header would serve the wrong one."""
    roster_rider(zwid=1001, name="Ada Racer")

    response = _get(auth_client, headers, page=1)

    assert "HX-Request" in response.headers["Vary"]


@pytest.mark.django_db
def test_the_search_form_searches_as_you_type(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")

    body = _get(auth_client).content.decode()
    form = body.split('role="search"', 1)[1].split("</form>", 1)[0]

    assert f'hx-get="{reverse("team:roster")}"' in form
    assert 'hx-trigger="input changed delay:300ms from:#roster-search, submit"' in form
    assert 'hx-target="#roster-results"' in form
    assert 'hx-swap="outerHTML"' in form
    # The server sets the URL (HX-Replace-Url); nothing here may push a history entry per keystroke.
    assert "hx-push-url" not in form
    assert "hx-replace-url" not in form
    assert 'hx-sync="this:replace"' in form


@pytest.mark.django_db
def test_the_page_keeps_riders_out_of_htmxs_local_storage(auth_client, roster_rider):
    """Without it, htmx saves the page -- names and faces -- in localStorage before a swap."""
    roster_rider(zwid=1001, name="Ada Racer")

    assert 'hx-history="false"' in _get(auth_client).content.decode()


@pytest.mark.django_db
def test_a_search_keeps_the_filters_in_force(auth_client, roster_rider):
    """The form carries them, so typing narrows within the filter instead of dropping it."""
    roster_rider(zwid=1001, name="Ada Gold", category_racing="Gold")
    roster_rider(zwid=1002, name="Ada Copper", category_racing="Copper")

    page = _get(auth_client, zr="Gold", page=2, q="old", category="", sort="").content.decode()
    form = page.split('role="search"', 1)[1].split("</form>", 1)[0]
    results = _get(auth_client, RESULTS, zr="Gold", q="ada").content.decode()

    assert '<input type="hidden" name="zr" value="Gold">' in form
    assert 'name="category"' not in form  # a blank select is not a filter in force
    assert 'name="sort"' not in form
    # Not the query (the box is the query) and not the page (a new search starts at the top).
    assert 'type="hidden" name="q"' not in form
    assert 'name="page"' not in form
    assert "Ada Gold" in results
    assert "Ada Copper" not in results


@pytest.mark.django_db
def test_the_filter_form_sends_the_query_only_when_there_is_one(auth_client, roster_rider):
    """A disabled input is not submitted, so an empty search adds no q= to the URL."""
    roster_rider(zwid=1001, name="Ada Racer")

    empty = _get(auth_client).content.decode()
    searched = _get(auth_client, q="ada").content.decode()

    assert '<input type="hidden" name="q" value="" id="roster-filters-q" disabled>' in empty
    assert '<input type="hidden" name="q" value="ada" id="roster-filters-q">' in searched


@pytest.mark.django_db
def test_a_live_search_is_not_logged_as_a_page_view_and_never_logs_the_query(auth_client, roster_rider, monkeypatch):
    roster_rider(zwid=1001, name="Ada Racer")
    lines = []
    for level in ("info", "debug"):
        monkeypatch.setattr(
            team_views.logfire, level, lambda message, _level=level, **kw: lines.append((_level, message, kw))
        )

    _get(auth_client, RESULTS, q="Lovelace")

    messages = [(level, message) for level, message, _ in lines]
    assert ("info", "Roster viewed") not in messages
    assert ("debug", "Roster results updated") in messages
    assert "Lovelace" not in repr(lines)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"q": "ada"}, "/team/roster/?q=ada"),
        ({"q": ""}, "/team/roster/"),
        ({"q": "  "}, "/team/roster/"),
        ({"q": "ada", "zr": "Gold", "page": "3"}, "/team/roster/?q=ada&zr=Gold"),
        ({"q": "", "zr": "Gold", "sort": ""}, "/team/roster/?zr=Gold"),
    ],
)
def test_a_live_search_sets_a_clean_address(auth_client, roster_rider, params, expected):
    """Clearing the box gives back the plain URL; a search always restarts at the top."""
    roster_rider(zwid=1001, name="Ada Racer")

    response = _get(auth_client, RESULTS, **params)

    assert response.headers["HX-Replace-Url"] == expected


@pytest.mark.django_db
def test_a_cleared_search_leaves_no_empty_query_on_the_page_links(auth_client, roster_rider, monkeypatch):
    _page_size(monkeypatch, 1)
    _riders(roster_rider, 2)

    body = _get(auth_client, RESULTS, q="", zr="").content.decode()
    href, _, _ = _more_link(body)

    assert href == f"{reverse('team:roster')}?page=2"


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, MORE])
def test_only_a_live_search_moves_the_address(auth_client, roster_rider, headers):
    roster_rider(zwid=1001, name="Ada Racer")

    assert "HX-Replace-Url" not in _get(auth_client, headers, q="ada", page=1).headers


@pytest.mark.django_db
def test_clearing_the_search_announces_everyone(auth_client, roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")
    roster_rider(zwid=1002, name="Bo Climber")

    body = _get(auth_client, RESULTS, q="").content.decode()

    assert '<p id="roster-announcer" hx-swap-oob="innerHTML">All 2 riders.</p>' in body


# --- "Show more riders" ------------------------------------------------------------------------


def _riders(roster_rider, count, *, prefix="Rider"):
    for n in range(count):
        roster_rider(zwid=5000 + n, name=f"{prefix} {n:03d}")


def _more_link(body):
    match = re.search(r'<a class="btn btn-sm"\s+href="([^"]+)"\s+hx-get="([^"]+)"[^>]*hx-trigger="([^"]+)"', body)
    assert match, "no Show more link"
    return match.groups()


@pytest.mark.django_db
def test_the_more_link_is_a_real_link_to_the_next_page(auth_client, roster_rider, monkeypatch):
    """Without JavaScript it simply opens the next page -- with the search and filters kept."""
    _page_size(monkeypatch, 2)
    _riders(roster_rider, 5)

    body = _get(auth_client, q="rider").content.decode()
    href, hx_get, _ = _more_link(body)

    assert href == hx_get
    assert href == f"{reverse('team:roster')}?q=rider&amp;page=2"
    assert 'hx-target="#roster-more"' in body
    assert 'hx-swap="outerHTML"' in body


@pytest.mark.django_db
def test_more_appends_the_next_page_under_the_cards_already_shown(auth_client, roster_rider, monkeypatch):
    _page_size(monkeypatch, 2)
    _riders(roster_rider, 5)

    body = _get(auth_client, MORE, page=2).content.decode()
    # Up to the announcer: each card holds lists of its own, so "</ul>" is no boundary.
    appended = body.split('<ul hx-swap-oob="beforeend:#roster-list">', 1)[1].split('id="roster-announcer"', 1)[0]

    assert "<html" not in body
    assert appended.count(CARD) == 2
    assert "Rider 002" in appended
    assert "Rider 003" in appended
    assert "Rider 001" not in body
    # The off-screen marker focus moves to, naming the range.
    assert '<li id="roster-from-3" tabindex="-1" class="sr-only">Riders 3 to 4 of 5</li>' in appended
    assert '<p id="roster-announcer" hx-swap-oob="innerHTML">Loaded riders 3 to 4 of 5.</p>' in body


@pytest.mark.django_db
def test_more_brings_the_button_for_the_page_after(auth_client, roster_rider, monkeypatch):
    _page_size(monkeypatch, 2)
    _riders(roster_rider, 5)

    body = _get(auth_client, MORE, page=2).content.decode()
    href, _, _ = _more_link(body)

    assert href.endswith("page=3")
    # Counted from the top, because these cards are added under the ones already shown.
    assert "4 of 5 shown" in body
    assert 'hx-on::before-request="rosterMoreRequested(event, 5)"' in body


@pytest.mark.django_db
def test_the_last_page_brings_no_button(auth_client, roster_rider, monkeypatch):
    _page_size(monkeypatch, 2)
    _riders(roster_rider, 5)

    body = _get(auth_client, MORE, page=3).content.decode()

    assert body.count(CARD) == 1
    assert 'id="roster-more"' not in body


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["4", "99", "banana", ""])
def test_a_page_that_no_longer_exists_brings_nothing(auth_client, roster_rider, monkeypatch, page):
    """The list may have shrunk since the button was drawn; appending the last page again would duplicate it."""
    _page_size(monkeypatch, 2)
    _riders(roster_rider, 5)

    response = _get(auth_client, MORE, page=page)

    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.django_db
def test_scrolling_loads_the_first_pages_by_itself_then_waits_to_be_asked(auth_client, roster_rider, monkeypatch):
    """Past ROSTER_AUTO_LOAD_PAGES the footer stays reachable and a phone is not handed the team."""
    _page_size(monkeypatch, 1)
    _riders(roster_rider, team_views.ROSTER_AUTO_LOAD_PAGES + 2)

    triggers = {}
    for page in range(1, team_views.ROSTER_AUTO_LOAD_PAGES + 2):
        headers = MORE if page > 1 else None
        triggers[page] = _more_link(_get(auth_client, headers, page=page).content.decode())[2]

    for page in range(1, team_views.ROSTER_AUTO_LOAD_PAGES):
        assert triggers[page] == "click, intersect once", page
    assert triggers[team_views.ROSTER_AUTO_LOAD_PAGES] == "click"
    assert triggers[team_views.ROSTER_AUTO_LOAD_PAGES + 1] == "click"


@pytest.mark.django_db
def test_the_page_defines_the_focus_handler_the_button_calls(auth_client, roster_rider, monkeypatch):
    _page_size(monkeypatch, 1)
    _riders(roster_rider, 2)

    body = _get(auth_client).content.decode()

    assert "function rosterMoreRequested(event, firstIndex)" in body
    assert "function rosterMoreFailed(event)" in body
    assert "htmx.onLoad(" in body
    assert 'hx-on::before-request="rosterMoreRequested(event, 2)"' in body
    assert 'hx-on::after-request="rosterMoreFailed(event)"' in body
    assert "trigger.type === 'click'" in body  # a scroll-started load leaves focus alone


@pytest.mark.django_db
def test_a_page_opened_on_its_own_says_where_it_is(auth_client, roster_rider, monkeypatch):
    _page_size(monkeypatch, 2)
    _riders(roster_rider, 5)

    body = _get(auth_client, page=2, q="rider").content.decode()

    assert "Showing 3&ndash;4 of 5." in body
    assert '<a class="link" href="?q=rider">Start from the first</a>' in body
    assert "4 of 5 shown" not in body  # only this page's riders are on it


@pytest.mark.django_db
def test_the_not_linked_list_has_more_too(client, membership_admin, monkeypatch):
    """It was cut at the first 48 with no way to see the rest."""
    _page_size(monkeypatch, 2)
    for n in range(3):
        GuildMember.objects.create(discord_id=f"80{n}", username=f"nolink{n}", user=None, is_bot=False)
    client.force_login(membership_admin)

    first = _get(client, link="no_account").content.decode()
    more = _get(client, MORE, link="no_account", page=2).content.decode()

    href, _, _ = _more_link(first)
    assert href.endswith("link=no_account&amp;page=2")
    assert ">Show more</a>" in first
    assert 'aria-label="More people"' in first
    assert "nolink2" in more.split('beforeend:#roster-list">', 1)[1]


# --- the shared index ----------------------------------------------------------------------------


@pytest.fixture
def counted_builds(monkeypatch):
    builds = []
    real = rosterv2.build_roster_index

    def counting(*args, **kwargs):
        builds.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(rosterv2, "build_roster_index", counting)
    return builds


@pytest.mark.django_db
def test_one_build_serves_every_request_for_a_minute(auth_client, roster_rider, counted_builds, monkeypatch):
    roster_rider(zwid=1001, name="Ada Racer")
    clock = [1000.0]
    monkeypatch.setattr(rosterv2.time, "monotonic", lambda: clock[0])

    _get(auth_client)
    _get(auth_client, RESULTS, q="a")
    _get(auth_client, MORE, page=1)
    clock[0] += rosterv2.ROSTER_INDEX_TTL_SECONDS - 1
    _get(auth_client, RESULTS, q="ad")
    assert len(counted_builds) == 1

    clock[0] += 2
    _get(auth_client, RESULTS, q="ada")
    assert len(counted_builds) == 2


@pytest.mark.django_db
def test_the_shared_index_is_built_for_nobody_in_particular(roster_rider, counted_builds):
    """Built with a reader, it would carry that reader's private signups to everyone."""
    roster_rider(zwid=1001, name="Ada Racer")

    rosterv2.roster_index_for(viewer_id=12345)

    assert counted_builds == [{}]


@pytest.mark.django_db
def test_concurrent_readers_wait_for_one_build(monkeypatch):
    builds = []

    def slow_build():
        builds.append(1)
        time.sleep(0.2)
        return rosterv2.RosterIndex()

    monkeypatch.setattr(rosterv2, "build_roster_index", slow_build)
    results = []
    threads = [threading.Thread(target=lambda: results.append(rosterv2.shared_roster_index())) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(builds) == 1
    assert len(results) == 5
    assert all(result is results[0] for result in results)


def _private_signup_setup(roster_rider, user_model):
    from apps.team.test_rosterv2_events import _event, _member

    roster_rider(zwid=4242, name="Ada Racer")
    return _member(user_model, "ada", 4242), _event("Private Selection", show_signups=False)


@pytest.mark.django_db
def test_your_own_new_signup_shows_at_once_however_old_the_shared_index(client, roster_rider, user_model):
    """The reader's own card is laid over the shared index fresh on every request."""
    from apps.events.models import EventSignup

    ada, event = _private_signup_setup(roster_rider, user_model)
    client.force_login(ada)
    assert "Private Selection" not in _get(client).content.decode()  # builds and keeps the index

    EventSignup.objects.create(event=event, user=ada, status=EventSignup.Status.REGISTERED)

    assert "Private Selection" in _get(client).content.decode()
    assert "Private Selection" in _get(client, RESULTS, q="ada").content.decode()


@pytest.mark.django_db
def test_your_private_signup_never_reaches_the_shared_index(client, roster_rider, user_model):
    from apps.events.models import EventSignup

    ada, event = _private_signup_setup(roster_rider, user_model)
    EventSignup.objects.create(event=event, user=ada, status=EventSignup.Status.REGISTERED)
    bo = user_model.objects.create_user(username="bo", permission_overrides={"team_member": True})

    client.force_login(ada)
    assert "Private Selection" in _get(client).content.decode()  # Ada's view is built first
    client.force_login(bo)

    body = _get(client).content.decode()
    assert "Ada Racer" in body
    assert "Private Selection" not in body


@pytest.mark.django_db
def test_a_reader_without_a_card_gets_the_shared_index_itself(roster_rider, user_model):
    roster_rider(zwid=1001, name="Ada Racer")
    stranger = user_model.objects.create_user(username="stranger")

    assert rosterv2.roster_index_for(stranger.pk) is rosterv2.shared_roster_index()
    assert rosterv2.roster_index_for(None) is rosterv2.shared_roster_index()
