"""Sorting the riders on a stage-series sheet's results page (``rider_sort``).

Guards who may sort by which figures -- a sort gives away a ranking, so a figure the viewer
may not see on the signups table must not reach the page at all -- how figures rank and
read, and that the stage answers the page sorts by are on the page.
"""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.events import rider_sort, series
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    SeriesStage,
    SheetStage,
    Squad,
    SquadMember,
)
from apps.zwiftpower.models import ZPTeamRiders
from conftest import _make_user


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the per-user badge caches: SQLite reuses pks between tests."""
    cache.clear()


@pytest.fixture
def event(db) -> Event:
    """Build a visible race-series event with two stages.

    Returns:
        The event.

    """
    today = date.today()
    event = Event.objects.create(
        title="Winter Series",
        start_date=today,
        end_date=today + timedelta(days=60),
        visible=True,
        availability_type=Event.AvailabilityType.SERIES,
    )
    SeriesStage.objects.create(event=event, order=0, name="Opener", stage_type="points", riders_min=2)
    SeriesStage.objects.create(event=event, order=1, name="Team TT", stage_type="ttt", riders_min=2)
    return event


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with a published series sheet timing both stages.

    Returns:
        The squad.

    """
    squad = Squad.objects.create(event=event, name="Alpha", gender="COED", squad_timezone="Europe/London")
    grid = AvailabilityGrid(
        squad=squad,
        kind=AvailabilityGrid.Kind.SERIES,
        status=AvailabilityGrid.Status.PUBLISHED,
        title="Alpha stages",
        grid_timezone="Europe/London",
    )
    starts = [datetime.combine(date.today() + timedelta(days=11 + 7 * i), time(19, 30), tzinfo=UTC) for i in (0, 1)]
    rows = [
        SheetStage(stage=stage, starts_at=when, racing=True)
        for stage, when in zip(event.series_stages.order_by("order"), starts, strict=True)
    ]
    series.sync_sheet_span(grid, rows)
    grid.save()
    for row in rows:
        row.grid = grid
        row.save()
    return squad


def _member(user_model, squad, username, **extra):
    """Add a team member to the squad.

    Returns:
        The user.

    """
    user = _make_user(user_model, username=username, permissions={"team_member": True}, **extra)
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _results(client, squad):
    """Open the squad's series results page.

    Returns:
        The page's HTML.

    """
    url = reverse("events:availability_results", args=[squad.event.pk, squad.pk, series.series_sheet(squad).pk])
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def _values(page: str) -> dict:
    """Read the riders' sort values from the page.

    Returns:
        ``{user pk: {key: [rank, shown]}}``.

    """
    found = re.search(r'<script id="rider-sort-values" type="application/json">(.*?)</script>', page, re.S)
    assert found, "no sort values on the page"
    return json.loads(found.group(1))


def _options(page: str) -> set[str]:
    """Read the sort menu's option values.

    Returns:
        The values.

    """
    menu = page[page.index('<select id="rider-sort"') : page.index("</select>", page.index('<select id="rider-sort"'))]
    return set(re.findall(r'<option value="([^"]*)"', menu))


@pytest.mark.django_db
def test_a_plain_member_sorts_only_by_what_the_popover_shows(client, squad, user_model, rider_profile_factory) -> None:
    """Power must not reach a page a plain member opens: a ranking gives it away even without numbers."""
    rider = _member(user_model, squad, "ann", zwid=101)
    rider.z_ftp = 300
    rider.save()
    rider_profile_factory(zwid=101, name="Ann", velo=1700.0, category_racing="Ruby")
    client.force_login(_member(user_model, squad, "bob"))

    page = _results(client, squad)

    assert _options(page) == {"", "stages", "zp_category", "zp_category_w", "velo1", "velo1_cat", "zr_v2_cat"}
    ann = _values(page)[str(rider.pk)]
    assert ann["velo1"] == [1700.0, "1,700"]
    assert set(ann) <= rider_sort.PUBLIC_KEYS
    assert "zftp" not in page.split('id="rider-sort-values"', 1)[1]


@pytest.mark.django_db
def test_those_who_see_the_full_signups_table_sort_by_power(client, squad, event_admin, user_model) -> None:
    rider = _member(user_model, squad, "ann", zwid=101)
    rider.z_ftp, rider.z_map = 300, 480
    rider.save()
    ZPTeamRiders.objects.create(zwid=101, name="Ann", ftp=280, weight=Decimal("70.0"), div=20)
    client.force_login(event_admin)

    page = _results(client, squad)
    ann = _values(page)[str(rider.pk)]

    assert {"ftp", "wkg", "zftp", "zmap", "zr_v2_race", "zr_wins"} <= _options(page)
    assert ann["ftp"] == [280.0, "280"]
    assert ann["wkg"] == [4.0, "4"]
    assert ann["zmap"] == [480.0, "480"]
    assert ann["zp_category"][1] == "B"


@pytest.mark.django_db
def test_a_squad_captain_elsewhere_in_the_event_also_gets_power(client, squad, event, user_model) -> None:
    """The signups table's rule: a captain of any squad in the event sees its full columns."""
    other = Squad.objects.create(event=event, name="Bravo", gender="COED")
    captain = _make_user(user_model, username="cap", permissions={"team_member": True})
    other.captains.add(captain)
    _member(user_model, squad, "ann")
    client.force_login(captain)

    assert "ftp" in _options(_results(client, squad))


@pytest.mark.django_db
def test_a_rider_with_no_figures_has_no_values(client, squad, event_admin, user_model) -> None:
    """Left out rather than zero, so the page can put them last whichever way it sorts."""
    rider = _member(user_model, squad, "ann")
    client.force_login(event_admin)

    assert _values(_results(client, squad))[str(rider.pk)] == {}


@pytest.mark.django_db
def test_each_answer_cell_carries_its_answer_for_the_stage_sort(client, squad, user_model) -> None:
    rider = _member(user_model, squad, "ann")
    grid = series.series_sheet(squad)
    first = grid.sheet_stages.order_by("starts_at").first()
    AvailabilityResponse.objects.create(
        grid=grid,
        user=rider,
        stage_answers={str(first.stage_id): {"answer": "available", "for": series.stamp(first.starts_at)}},
    )
    client.force_login(rider)

    page = _results(client, squad)
    row = page[page.index(f'data-user-id="{rider.pk}"') :].split("</tr>", 1)[0]

    assert re.findall(r'data-answer="([^"]*)"', row) == ["available", "none"]
    assert 'data-stage-sort="0"' in page
    assert 'data-stage-sort="1"' in page


@pytest.mark.django_db
def test_the_squads_together_cards_leave_the_sort_out(client, squad, event_admin, user_model) -> None:
    _member(user_model, squad, "ann")
    client.force_login(event_admin)

    page = client.get(reverse("events:series_squads", args=[squad.event.pk]), {"squad": [squad.pk]}).content.decode()

    assert "Alpha" in page
    assert 'id="rider-sort"' not in page
    assert "data-stage-sort" not in page


def test_categories_rank_best_first_and_unknown_ones_last() -> None:
    rank = rider_sort._rank

    assert rank("velo1_cat", "Diamond") > rank("velo1_cat", "Ruby") > rank("velo1_cat", "Copper")
    assert rank("velo1_cat", "Copper") > rank("velo1_cat", "Mystery")
    assert rank("zp_category", "A+") > rank("zp_category", "A") > rank("zp_category", "E")
    assert rank("ftp", "") is None
    assert rank("ftp", None) is None


def test_figures_read_as_on_the_signups_table() -> None:
    shown = rider_sort._shown

    assert shown(1642) == "1,642"
    assert shown(Decimal("4.10")) == "4.1"
    assert shown(3.0) == "3"
    assert shown(-2.35) == "-2.35"
    assert shown("Ruby") == "Ruby"


def test_every_category_option_is_marked_as_one() -> None:
    """The page labels a category sort "Best first", a number "Highest first"."""
    event = Event(title="X", start_date=date.today(), end_date=date.today())
    groups, _ = rider_sort.sort_data(event, [], full=True)
    marked = {option.key for group in groups for option in group.options if option.category}

    assert {"zp_category", "velo1_cat", "velo1_cat_w", "zr_v2_cat"} <= marked
    assert not marked & {"ftp", "velo1", "zr_v2_race"}
