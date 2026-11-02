"""The ways to a race series' board, besides the event page's Actions menu.

The board is for the people running the series (``_can_view_v_report``). The event page
shows it in a tab of its own, before Races, fetched the first time the tab opens; Manage
Squads lists it under Reports; the Participation page has a button to it. Each appears only
on a series and only for someone the board admits, except that Manage Squads greys it out,
as its menu does every item a viewer's roles don't cover.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad
from apps.events.series_views import BOARD_PANEL_ID
from conftest import _make_user

TAB_HEADERS = {"HX-Request": "true", "HX-Target": BOARD_PANEL_ID}


def _event(**extra) -> Event:
    """Build a visible event with one squad.

    Returns:
        The event.

    """
    today = date.today()
    event = Event.objects.create(
        title="Winter Series", start_date=today, end_date=today + timedelta(days=60), visible=True, **extra
    )
    Squad.objects.create(event=event, name="A1")
    return event


@pytest.fixture
def series_event(db) -> Event:
    """Build a race-series event.

    Returns:
        The event.

    """
    return _event(availability_type=Event.AvailabilityType.SERIES)


@pytest.fixture
def weekly_event(db) -> Event:
    """Build a weekly-availability event.

    Returns:
        The event.

    """
    return _event()


def _board_url(event: Event) -> str:
    return reverse("events:series_board", args=[event.pk])


def _page(client, name: str, event: Event, **params) -> str:
    response = client.get(reverse(name, args=[event.pk]), params)
    assert response.status_code == 200
    return response.content.decode()


# --- The event page's tab ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_series_has_a_series_board_tab_before_races(client, series_event, event_admin):
    client.force_login(event_admin)

    body = _page(client, "events:event_detail", series_event)

    assert re.findall(r'<a role="tab"[^>]*data-tab="([a-z]+)"', body) == [
        "signups",
        "squads",
        "category",
        "series",
        "races",
    ]
    assert 'aria-controls="panel-series"' in body
    assert 'aria-labelledby="tab-series"' in body


@pytest.mark.django_db
def test_the_tab_fetches_the_board_when_it_is_first_shown(client, series_event, event_admin):
    client.force_login(event_admin)

    body = _page(client, "events:event_detail", series_event)

    panel = re.search(rf'<div id="{BOARD_PANEL_ID}"[^>]*>', body).group(0)
    assert f'hx-get="{_board_url(series_event)}"' in panel
    assert 'hx-trigger="intersect once"' in panel
    # Until it loads, or with no JavaScript at all, the board is still a link away.
    placeholder = body[body.index(f'id="{BOARD_PANEL_ID}"') : body.index('data-tab-panel="races"')]
    assert f'href="{_board_url(series_event)}"' in placeholder


@pytest.mark.django_db
def test_no_tab_for_a_member_the_board_would_refuse(client, series_event, team_member):
    client.force_login(team_member)

    body = _page(client, "events:event_detail", series_event)

    assert 'data-tab="series"' not in body
    assert f'id="{BOARD_PANEL_ID}"' not in body


@pytest.mark.django_db
def test_no_tab_on_a_weekly_event(client, weekly_event, event_admin):
    client.force_login(event_admin)

    body = _page(client, "events:event_detail", weekly_event)

    assert 'data-tab="series"' not in body


# --- The board, answering the tab --------------------------------------------------------------


@pytest.mark.django_db
def test_the_board_answers_the_tab_with_itself_alone(client, series_event, event_admin):
    client.force_login(event_admin)

    response = client.get(_board_url(series_event), headers=TAB_HEADERS)

    assert response.status_code == 200
    templates = {template.name for template in response.templates}
    assert "events/_series_board.html" in templates
    assert "base.html" not in templates
    body = response.content.decode()
    assert "Back to Event" not in body
    assert '<h3 class="sr-only">Series board</h3>' in body
    assert "HX-Request" in response["Vary"]


@pytest.mark.django_db
def test_choosing_times_in_the_tab_reloads_only_the_tab(client, series_event, event_admin):
    client.force_login(event_admin)

    body = client.get(_board_url(series_event), headers=TAB_HEADERS).content.decode()
    form = re.search(r"<form[^>]*>", body).group(0)

    assert f'hx-target="#{BOARD_PANEL_ID}"' in form
    assert 'hx-trigger="change"' in form
    assert "onchange" not in form
    mine = client.get(_board_url(series_event), {"tz": "mine"}, headers=TAB_HEADERS)
    assert mine.context["mine"] is True
    assert mine.context["embedded"] is True
    # Focus goes back to the chosen radio after the swap only because each keeps an id.
    assert re.search(r'<input type="radio" id="board-tz-mine"[^>]*checked', mine.content.decode())


@pytest.mark.django_db
def test_the_board_page_still_stands_alone(client, series_event, event_admin):
    client.force_login(event_admin)

    response = client.get(_board_url(series_event))

    assert "events/series_board.html" in {template.name for template in response.templates}
    body = response.content.decode()
    assert "Back to Event" in body
    assert "<h1" in body
    assert 'onchange="this.submit()"' in body


@pytest.mark.django_db
def test_a_refused_tab_gets_a_status_rather_than_the_event_page(client, series_event, team_member):
    """A redirect would be followed, filling the tab with the event page it lands on."""
    client.force_login(team_member)

    assert client.get(_board_url(series_event), headers=TAB_HEADERS).status_code == 403


@pytest.mark.django_db
def test_a_weekly_event_answers_the_tab_with_not_found(client, weekly_event, event_admin):
    client.force_login(event_admin)

    assert client.get(_board_url(weekly_event), headers=TAB_HEADERS).status_code == 404


# --- Manage Squads -----------------------------------------------------------------------------


def _reports(body: str) -> str:
    start = body.index('<li class="menu-title">Reports</li>')
    return body[start : body.index('<li class="menu-title">Manage</li>', start)]


@pytest.mark.django_db
def test_manage_squads_lists_the_board_under_reports(client, series_event, event_admin):
    client.force_login(event_admin)

    reports = _reports(_page(client, "events:squad_manage", series_event))

    assert f'href="{_board_url(series_event)}"' in reports
    assert reports.index("Participation") < reports.index("Series board")


@pytest.mark.django_db
def test_manage_squads_greys_it_out_for_a_discord_captain_role_holder(client, series_event, user_model):
    """The squad's Discord captain role opens Manage Squads, but not the board."""
    squad = series_event.squads.get()
    squad.discord_captain_role = 777
    squad.save(update_fields=["discord_captain_role"])
    holder = _make_user(
        user_model, username="role_captain", permissions={"team_member": True}, discord_roles={"777": "A1 Captain"}
    )
    client.force_login(holder)

    reports = _reports(_page(client, "events:squad_manage", series_event))

    assert "Series board" in reports
    assert _board_url(series_event) not in reports
    assert '<li class="menu-disabled">' in reports


@pytest.mark.django_db
def test_manage_squads_has_no_board_on_a_weekly_event(client, weekly_event, event_admin):
    client.force_login(event_admin)

    assert "Series board" not in _reports(_page(client, "events:squad_manage", weekly_event))


# --- The Participation page --------------------------------------------------------------------


@pytest.mark.django_db
def test_the_participation_page_links_the_board(client, series_event, event_admin):
    client.force_login(event_admin)

    body = _page(client, "events:event_all_races", series_event, tab="participation")

    assert f'href="{_board_url(series_event)}"' in body


@pytest.mark.django_db
def test_the_participation_page_keeps_the_board_from_other_members(client, series_event, team_member):
    """Anyone who can see the event sees this page; the board is for those running it."""
    client.force_login(team_member)

    assert _board_url(series_event) not in _page(client, "events:event_all_races", series_event, tab="participation")


@pytest.mark.django_db
def test_the_participation_page_has_no_board_on_a_weekly_event(client, weekly_event, event_admin):
    client.force_login(event_admin)

    assert _board_url(weekly_event) not in _page(client, "events:event_all_races", weekly_event, tab="participation")
