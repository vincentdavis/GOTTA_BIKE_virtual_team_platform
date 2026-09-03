"""The availability column headings on the participation report.

Two things happen in a table header that is one row of dates: it is read horizontally, so
anything repeated in every column is noise, and it is the only place a per-column total can
go. Hence a bare date and a tally under it.

The interesting case is the one the simplification breaks -- two sheets on the same day --
which is why the time is not simply deleted.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    Event,
    EventSignup,
    Squad,
    SquadMember,
)


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today - timedelta(days=1), end_date=today + timedelta(days=90), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad on the event.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Affinity")


def _rider(user_model, squad, name: str):
    """Add a registered squad member.

    Args:
        user_model: The active user model.
        squad: The squad to join.
        name: First name, also used for the username.

    Returns:
        The rider.

    """
    user = user_model.objects.create_user(
        username=name.lower(), email=f"{name.lower()}@example.test", first_name=name, last_name="Rider"
    )
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    EventSignup.objects.create(event=squad.event, user=user, status=EventSignup.Status.REGISTERED)
    return user


def _slot_grid(squad, when: date, at: str = "06:00") -> AvailabilityGrid:
    """Build a published single-slot sheet.

    Args:
        squad: The owning squad.
        when: The slot's date.
        at: The slot's UTC time.

    Returns:
        The grid.

    """
    return AvailabilityGrid.objects.create(
        squad=squad, start_date=when, end_date=when, start_time=at, end_time="23:59",
        slot_duration=60, grid_timezone="UTC", single_slot=True,
        status=AvailabilityGrid.Status.PUBLISHED,
    )


def _answer(grid, user, *, available: bool) -> None:
    """Record a rider's response to a sheet.

    Args:
        grid: The sheet.
        user: The rider.
        available: Whether they said yes.

    """
    AvailabilityResponse.objects.create(
        grid=grid, user=user, available_cells=["2026-09-22|06:00"] if available else []
    )


def _body(client, viewer, event) -> str:
    """Render the participation tab.

    Args:
        client: Test client.
        viewer: The signed-in user.
        event: The event.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(
        reverse("events:event_all_races", args=[event.pk]), {"tab": "participation"}
    ).content.decode()


def _header(body: str) -> str:
    """Slice every table header, where the column labels and tallies live.

    One table per squad, so a single-slice helper silently reads only the first squad's
    columns -- which is how a cross-squad assertion can fail while the page is correct.

    Args:
        body: The rendered page.

    Returns:
        The concatenated thead markup, attributes included.

    """
    sections = re.findall(r"<thead>.*?</thead>", body, re.S)
    assert sections, "no table header found"
    joined = "\n".join(sections)
    assert len(joined) < len(body) / 2, "header slice widened to the whole page"
    return joined


def _header_text(body: str) -> str:
    """Return only what a reader actually SEES in the table header.

    Tag-stripped on purpose. The time survives in each column's ``title`` tooltip by
    design, so asserting against raw markup would report the label as still carrying a
    time when the visible heading is exactly what was asked for.

    Args:
        body: The rendered page.

    Returns:
        The header's visible text, whitespace-collapsed.

    """
    return " ".join(re.sub(r"<[^>]*>", " ", _header(body)).split())


# --- the date, without the time --------------------------------------------------------


@pytest.mark.django_db
def test_a_single_slot_column_shows_the_date_without_a_time(client, team_member, squad, user_model):
    """In a weekly series every column carried the same clock time -- pure noise, and wide."""
    _rider(user_model, squad, "Ana")
    _slot_grid(squad, date(2026, 9, 22))

    text = _header_text(_body(client, team_member, squad.event))

    assert "Sep 22" in text
    assert "06:00" not in text


@pytest.mark.django_db
def test_the_day_is_not_zero_padded(client, team_member, squad, user_model):
    """"Sep 02" beside "Sep 22" is a wider column for nothing; matches the race badges."""
    _rider(user_model, squad, "Ana")
    _slot_grid(squad, date(2026, 9, 2))

    text = _header_text(_body(client, team_member, squad.event))

    assert "Sep 2" in text
    assert "Sep 02" not in text


@pytest.mark.django_db
def test_the_dropped_time_is_still_on_hover(client, team_member, squad, user_model):
    """Compact should not mean lossy -- a captain checking a start time needs it somewhere."""
    _rider(user_model, squad, "Ana")
    _slot_grid(squad, date(2026, 9, 22))

    assert "06:00" in _body(client, team_member, squad.event)


@pytest.mark.django_db
def test_two_sheets_on_one_day_keep_their_times(client, team_member, squad, user_model):
    """Otherwise the simplification produces two columns with identical headings.

    This is the case that makes deleting the time outright wrong: a morning and an evening
    race on the same date would be indistinguishable, and the tally under each would look
    like a contradiction rather than two different sheets.
    """
    _rider(user_model, squad, "Ana")
    _slot_grid(squad, date(2026, 9, 22), at="06:00")
    _slot_grid(squad, date(2026, 9, 22), at="18:00")

    text = _header_text(_body(client, team_member, squad.event))

    assert "Sep 22 06:00" in text
    assert "Sep 22 18:00" in text


@pytest.mark.django_db
def test_distinct_days_stay_short_when_another_squad_collides(client, team_member, event, user_model):
    """Disambiguation is per squad -- one squad's double-header must not widen everyone's columns."""
    busy = Squad.objects.create(event=event, name="Busy")
    calm = Squad.objects.create(event=event, name="Calm")
    _rider(user_model, busy, "Ana")
    _rider(user_model, calm, "Bo")
    _slot_grid(busy, date(2026, 9, 22), at="06:00")
    _slot_grid(busy, date(2026, 9, 22), at="18:00")
    _slot_grid(calm, date(2026, 9, 22), at="06:00")

    text = _header_text(_body(client, team_member, event))

    # Three headings across two tables; only the two that collide carry a time.
    assert text.count("Sep 22 06:00") == 1  # busy's morning sheet
    assert "Sep 22 18:00" in text  # busy's evening sheet
    assert re.search(r"Sep 22(?! \d\d:)", text), "calm's lone sheet should have kept the short label"


# --- the tally under the date ----------------------------------------------------------


@pytest.mark.django_db
def test_the_column_counts_who_said_yes(client, team_member, squad, user_model):
    """The question a captain opens this page with is "how many for that one?"."""
    yes_a = _rider(user_model, squad, "Ana")
    yes_b = _rider(user_model, squad, "Bea")
    no_one = _rider(user_model, squad, "Cal")
    _rider(user_model, squad, "Dee")  # never answered
    grid = _slot_grid(squad, date(2026, 9, 22))
    _answer(grid, yes_a, available=True)
    _answer(grid, yes_b, available=True)
    _answer(grid, no_one, available=False)

    assert "2 of 4" in _header_text(_body(client, team_member, squad.event))


@pytest.mark.django_db
def test_a_column_nobody_answered_reads_zero(client, team_member, squad, user_model):
    """Silence is the state worth seeing; an empty cell would read as "not counted yet"."""
    _rider(user_model, squad, "Ana")
    _slot_grid(squad, date(2026, 9, 22))

    assert "0 of 1" in _header_text(_body(client, team_member, squad.event))


@pytest.mark.django_db
def test_each_column_is_counted_separately(client, team_member, squad, user_model):
    """One shared total would be worse than none -- the whole point is comparing dates."""
    ana = _rider(user_model, squad, "Ana")
    bea = _rider(user_model, squad, "Bea")
    first = _slot_grid(squad, date(2026, 9, 22))
    second = _slot_grid(squad, date(2026, 9, 29))
    _answer(first, ana, available=True)
    _answer(first, bea, available=True)
    _answer(second, ana, available=True)
    _answer(second, bea, available=False)

    text = _header_text(_body(client, team_member, squad.event))

    assert "2 of 2" in text
    assert "1 of 2" in text


@pytest.mark.django_db
def test_the_tally_counts_only_this_squad(client, team_member, event, user_model):
    """A squad's column must not be inflated by riders the reader cannot see in that table."""
    mine = Squad.objects.create(event=event, name="Mine")
    other = Squad.objects.create(event=event, name="Other")
    ana = _rider(user_model, mine, "Ana")
    bo = _rider(user_model, other, "Bo")
    grid = _slot_grid(mine, date(2026, 9, 22))
    _answer(grid, ana, available=True)
    _answer(grid, bo, available=True)  # not in this squad

    assert "1 of 1" in _header_text(_body(client, team_member, event))
