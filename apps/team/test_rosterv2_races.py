"""Counting a rider's racing, with the one rule the owner was explicit about.

Group rides are not racing. ZwiftPower makes that harder than it sounds, because ``f_t`` is
a space-separated SET of flags rather than a type: a women's race is "TYPE_RACE TYPE_WOMENS",
a team time trial is "TYPE_TEAM_TIME_TRIAL TYPE_RACE", and five rows in the dev copy carry
both TYPE_RIDE and TYPE_RACE. Every one of those combinations is pinned below, because each
is a different way to get the count wrong and none of them looks wrong on the page.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.team.rosterv2 import (
    QUIET_DAYS,
    RACE_WINDOW_DAYS,
    apply_filters,
    build_roster_index,
    parse_filters,
    race_records,
    sort_rows,
)
from apps.zwiftpower.models import ZPEvent, ZPRiderResults


def _result(zwid, f_t, *, days_ago=1, position_in_cat=10, zid=None):
    """Record one result for a rider, at an event that many days ago.

    Returns:
        The created result.

    """
    zid = zid if zid is not None else ZPEvent.objects.count() + 9000
    event, _ = ZPEvent.objects.get_or_create(
        zid=zid,
        defaults={"title": f"Event {zid}", "event_date": timezone.now() - timedelta(days=days_ago)},
    )
    return ZPRiderResults.objects.create(
        event=event, zid=zid, zwid=zwid, name="Rider", f_t=f_t,
        pos=position_in_cat, position_in_cat=position_in_cat,
    )


def _record(zwid=1001):
    return race_records([zwid]).get(zwid)


# --- what counts as what ------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("f_t", "field"),
    [
        ("TYPE_RACE", "races"),
        # Matching f_t by equality drops every women's race -- 576 of them in the dev copy,
        # and a bug this app ships today at apps/zwiftpower/views.py.
        ("TYPE_RACE TYPE_WOMENS", "races"),
        ("TYPE_TIME_TRIAL", "time_trials"),
        ("TYPE_TIME_TRIAL TYPE_WOMENS", "time_trials"),
        # A team TT carries TYPE_RACE as well. Counting it under both would pad a rider's
        # race count with their own time trials.
        ("TYPE_TEAM_TIME_TRIAL", "time_trials"),
        ("TYPE_TEAM_TIME_TRIAL TYPE_RACE", "time_trials"),
        ("TYPE_TEAM_TIME_TRIAL TYPE_RACE TYPE_WOMENS", "time_trials"),
        ("TYPE_RIDE", "rides"),
        ("TYPE_RIDE TYPE_WOMENS", "rides"),
    ],
)
def test_each_event_type_is_counted_once_and_in_the_right_column(f_t, field):
    _result(1001, f_t)

    record = _record()

    assert getattr(record, field) == 1
    others = {"races", "time_trials", "rides"} - {field}
    assert [getattr(record, name) for name in others] == [0, 0]


@pytest.mark.django_db
@pytest.mark.parametrize("f_t", ["TYPE_RIDE TYPE_RACE", "TYPE_RIDE TYPE_RACE TYPE_WOMENS"])
def test_an_event_flagged_both_ride_and_race_counts_as_a_race(f_t):
    """A race, because the explicit flag wins over the default one.

    Five rows in the dev copy carry both. TYPE_RACE is a positive assertion; TYPE_RIDE is the
    weaker default. Either reading is defensible -- what matters is that it is decided and
    pinned rather than whatever the clause order happened to produce.
    """
    _result(1001, f_t)

    record = _record()

    assert record.races == 1
    assert record.rides == 0


@pytest.mark.django_db
@pytest.mark.parametrize("f_t", ["TYPE_WORKOUT", "TYPE_RUN", ""])
def test_a_workout_or_a_run_is_neither_a_race_nor_a_ride(f_t):
    _result(1001, f_t)

    record = _record()

    assert (record.races, record.time_trials, record.rides) == (0, 0, 0)


@pytest.mark.django_db
def test_group_rides_are_counted_but_never_ranked():
    """The owner's rule: rank on races and time trials, show the rides beside them."""
    for _ in range(5):
        _result(1001, "TYPE_RIDE")
    _result(1001, "TYPE_RACE")

    record = _record()

    assert record.rides == 5
    assert record.races == 1
    assert record.competitive == 1, "rides must not enter the figure the roster ranks on"


# --- the window ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_only_results_inside_the_window_are_counted():
    _result(1001, "TYPE_RACE", days_ago=1)
    _result(1001, "TYPE_RACE", days_ago=RACE_WINDOW_DAYS - 1)
    _result(1001, "TYPE_RACE", days_ago=RACE_WINDOW_DAYS + 5)

    assert _record().races == 2


@pytest.mark.django_db
def test_a_result_dated_in_the_future_is_not_counted():
    """A scheduled event with a date ahead of us would otherwise inflate the window."""
    _result(1001, "TYPE_RACE", days_ago=-5)

    assert _record().races == 0


@pytest.mark.django_db
def test_the_last_result_is_not_windowed():
    """"Last raced 4 months ago" is the useful answer; an empty window says nothing at all."""
    _result(1001, "TYPE_RACE", days_ago=120)

    record = _record()

    assert record.races == 0
    assert record.last_result_at is not None


# --- podiums --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_podium_is_a_podium_in_the_riders_own_category():
    for place in (1, 2, 3, 4):
        _result(1001, "TYPE_RACE", position_in_cat=place)

    record = _record()

    assert record.podiums == 3
    assert record.wins == 1


@pytest.mark.django_db
def test_a_group_ride_placing_is_not_a_podium():
    _result(1001, "TYPE_RIDE", position_in_cat=1)

    record = _record()

    assert record.podiums == 0
    assert record.wins == 0


@pytest.mark.django_db
def test_a_time_trial_podium_counts():
    _result(1001, "TYPE_TEAM_TIME_TRIAL TYPE_RACE", position_in_cat=2)

    assert _record().podiums == 1


# --- on the card ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_counts_reach_the_card(auth_client, roster_rider):
    roster_rider(zwid=4242, name="Ada Racer")
    for _ in range(3):
        _result(4242, "TYPE_RACE")
    _result(4242, "TYPE_RIDE")
    _result(4242, "TYPE_RACE", position_in_cat=1)

    body = auth_client.get(reverse("team:rosterv2")).content.decode()
    card = body.split('class="card bg-base-100', 1)[1]

    # Derived from the constant, so widening the window does not leave the tile lying.
    assert f"Races {RACE_WINDOW_DAYS}d" in card
    assert "+1 ride" in card
    assert "1 podium" in card


@pytest.mark.django_db
def test_our_own_results_date_the_last_race_over_the_cached_one(roster_rider):
    """The cached profile's date only moves when somebody presses Update; ours is synced."""
    roster_rider(zwid=4242, name="Ada Racer", days_since_race=200)
    _result(4242, "TYPE_RACE", days_ago=3)

    card = build_roster_index().rows[0].card

    assert (timezone.now() - card.last_race_at).days < 10


@pytest.mark.django_db
def test_a_rider_with_no_results_keeps_the_cached_date(roster_rider):
    """Results exist for well under half the roster; a date we hold beats no date at all."""
    roster_rider(zwid=4242, name="Ada Racer", days_since_race=200)

    assert build_roster_index().rows[0].card.last_race_at is not None


# --- sorting and filtering on racing ----------------------------------------------------------


@pytest.mark.django_db
def test_the_page_opens_on_the_riders_who_are_racing(roster_rider):
    """The default sort, which is the whole point of the page per the plan."""
    roster_rider(zwid=1001, name="Ada Quiet")
    roster_rider(zwid=1002, name="Zoe Busy")
    for _ in range(4):
        _result(1002, "TYPE_RACE")

    rows = sort_rows(list(build_roster_index().rows), "races", "")

    assert [row.card.name for row in rows] == ["Zoe Busy", "Ada Quiet"]


@pytest.mark.django_db
def test_the_page_with_no_sort_chosen_leads_with_the_racers(auth_client, roster_rider):
    """Through the view, with no sort named in the URL.

    A test that passes "races" to sort_rows cannot see DEFAULT_SORT change, which is the
    thing that decides what a reader arriving at the page actually gets.
    """
    roster_rider(zwid=1001, name="Ada Quiet")
    roster_rider(zwid=1002, name="Zoe Busy")
    for _ in range(4):
        _result(1002, "TYPE_RACE")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert body.index("Zoe Busy") < body.index("Ada Quiet")


@pytest.mark.django_db
def test_rides_do_not_lift_a_rider_up_the_default_sort(roster_rider):
    roster_rider(zwid=1001, name="Ada Racer")
    _result(1001, "TYPE_RACE")
    roster_rider(zwid=1002, name="Zoe Rider")
    for _ in range(9):
        _result(1002, "TYPE_RIDE")

    rows = sort_rows(list(build_roster_index().rows), "races", "")

    assert [row.card.name for row in rows] == ["Ada Racer", "Zoe Rider"]


@pytest.mark.django_db
def test_the_racing_filter_finds_the_recent_and_the_quiet(roster_rider):
    roster_rider(zwid=1001, name="Recent Racer", days_since_race=None)
    _result(1001, "TYPE_RACE", days_ago=5)
    roster_rider(zwid=1002, name="Quiet Racer", days_since_race=None)
    _result(1002, "TYPE_RACE", days_ago=QUIET_DAYS + 30)
    roster_rider(zwid=1003, name="Never Raced", days_since_race=None)

    index = build_roster_index()

    def names(**params):
        return sorted(r.card.name for r in apply_filters(list(index.rows), parse_filters(params, index.rows)))

    assert names(racing="30") == ["Recent Racer"]
    assert names(racing="90") == ["Recent Racer"]
    # A rider with no race on record is quiet, not a third state to be hidden.
    assert names(racing="quiet") == ["Never Raced", "Quiet Racer"]


@pytest.mark.django_db
def test_counting_races_costs_the_same_however_many_riders(roster_rider):
    """Two queries, whatever the roster size -- results are read in bulk, never per rider."""
    for n in range(3):
        roster_rider(zwid=1000 + n, name=f"Rider {n}")
        _result(1000 + n, "TYPE_RACE")
    build_roster_index()
    with CaptureQueriesContext(connection) as few:
        build_roster_index()

    for n in range(3, 25):
        roster_rider(zwid=1000 + n, name=f"Rider {n}")
        _result(1000 + n, "TYPE_RACE")
    with CaptureQueriesContext(connection) as many:
        build_roster_index()

    assert len(many) == len(few)


def test_the_counting_window_is_ninety_days():
    """The agreed window, pinned so changing it is a decision rather than a drive-by edit.

    It was 30 until Vincent widened it. Everything downstream -- the tile label, the sort
    labels, the cutoff -- reads this constant, so this is the only place the number appears.
    """
    assert RACE_WINDOW_DAYS == 90


@pytest.mark.django_db
def test_the_sort_option_names_the_window(auth_client, roster_rider):
    """Otherwise the page offers "Team races" and never says over what period."""
    roster_rider(zwid=1001, name="Ada Racer")

    body = auth_client.get(reverse("team:rosterv2")).content.decode()

    assert f"Team races ({RACE_WINDOW_DAYS} days)" in body
