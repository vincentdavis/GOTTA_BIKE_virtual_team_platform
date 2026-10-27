"""Race-series availability: stages, each squad's stage times, and per-stage answers.

An answer records the start time it was given for, so moving a stage asks again only the
riders who answered it -- these tests hold that rule, the scheduled race that moves with
its stage, and every page that reads a series sheet: the rider's form, the captain's
results, the coordinators' board, My Events, the badge and the participation report.
"""

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.events import series
from apps.events.context_processors import pending_availability_count
from apps.events.models import (
    AvailabilityGrid,
    AvailabilityResponse,
    AvailabilitySlotSelection,
    Event,
    SeriesStage,
    SheetStage,
    Squad,
    SquadMember,
)
from apps.zwift_data.models import ZwiftRoute
from conftest import _make_user

SERIES = AvailabilityGrid.Kind.SERIES
PUBLISHED = AvailabilityGrid.Status.PUBLISHED


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the pending badge's per-user cache: SQLite reuses pks between tests."""
    cache.clear()


@pytest.fixture
def event(db) -> Event:
    """Build a visible race-series event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Winter Series",
        start_date=today,
        end_date=today + timedelta(days=60),
        visible=True,
        availability_type=Event.AvailabilityType.SERIES,
    )


@pytest.fixture
def route(db) -> ZwiftRoute:
    """Build a route.

    Returns:
        The route.

    """
    return ZwiftRoute.objects.create(
        name="Volcano Circuit", world="Watopia", world_id=1, name_hash="volcano", distance_km=4.1, ascent_m=20
    )


@pytest.fixture
def stages(event, route) -> list[SeriesStage]:
    """Build three stages: a points race on a route, a TTT and a climb.

    Returns:
        The stages, in order.

    """
    first = date.today() + timedelta(days=10)
    return [
        SeriesStage.objects.create(
            event=event,
            order=0,
            name="Opener",
            stage_type="points",
            route=route,
            laps=3,
            window_start=first,
            window_end=first + timedelta(days=6),
            riders_min=2,
        ),
        SeriesStage.objects.create(
            event=event,
            order=1,
            name="Team TT",
            stage_type="ttt",
            window_start=first + timedelta(days=7),
            window_end=first + timedelta(days=13),
            riders_min=2,
        ),
        SeriesStage.objects.create(event=event, order=2, name="Climb", stage_type="climb"),
    ]


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad on the series.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="A1", discord_channel_id=555)


def _member(user_model, squad, username, **extra):
    """Add a team member to the squad.

    Returns:
        The user.

    """
    user = _make_user(user_model, username=username, permissions={"team_member": True}, **extra)
    SquadMember.objects.create(squad=squad, user=user, status=SquadMember.Status.MEMBER)
    return user


def _when(days: int, hour: int = 19, minute: int = 30) -> datetime:
    """Build a UTC start ``days`` from today.

    Returns:
        The datetime.

    """
    return datetime.combine(date.today() + timedelta(days=days), time(hour, minute), tzinfo=UTC)


def _sheet(squad, stages, starts, *, status=PUBLISHED) -> AvailabilityGrid:
    """Build a series sheet; a None start means the squad is not racing that stage.

    Returns:
        The sheet.

    """
    grid = AvailabilityGrid(squad=squad, kind=SERIES, status=status, title="A1 stages", grid_timezone="UTC")
    rows = [
        SheetStage(stage=stage, starts_at=starts_at, racing=starts_at is not None)
        for stage, starts_at in zip(stages, starts, strict=True)
    ]
    series.sync_sheet_span(grid, rows)
    grid.save()
    for row in rows:
        row.grid = grid
        row.save()
    return grid


def _answer(grid, user, stage, answer, *, at=None, note=""):
    """Store a rider's answer to one stage, given for ``at`` (default: its current time).

    Returns:
        The response.

    """
    sheet_stage = grid.sheet_stages.get(stage=stage)
    response, _ = AvailabilityResponse.objects.get_or_create(grid=grid, user=user)
    entry = {"answer": answer, "for": series.stamp(at or sheet_stage.starts_at)}
    if note:
        entry["note"] = note
    response.stage_answers = {**response.stage_answers, str(stage.pk): entry}
    response.save()
    return response


# --- The rules in apps.events.series --------------------------------------------------------


@pytest.mark.django_db
def test_an_answer_goes_stale_when_its_stage_moves(user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    _answer(grid, rider, stages[0], "available")
    sheet_stage = grid.sheet_stages.get(stage=stages[0])
    answers = AvailabilityResponse.objects.get(grid=grid, user=rider).stage_answers

    assert series.answer_state(answers, sheet_stage) == "available"
    sheet_stage.starts_at = _when(12)
    assert series.answer_state(answers, sheet_stage) == series.STALE
    sheet_stage.starts_at = _when(11)  # moved back: the answer counts again
    assert series.answer_state(answers, sheet_stage) == "available"


@pytest.mark.django_db
def test_a_stage_that_has_started_is_no_longer_owed_an_answer(squad, stages) -> None:
    grid = _sheet(squad, stages, [_when(-1), _when(18), None])
    sheet_stages = list(grid.sheet_stages.all())
    answers = {str(stages[1].pk): {"answer": "no", "for": series.stamp(_when(18))}}

    assert series.needs_answer(sheet_stages, answers)
    assert not series.needs_answer(sheet_stages, answers, after=timezone.now())


@pytest.mark.django_db
def test_two_stages_swapping_times_swap_their_races(squad, stages) -> None:
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    first = AvailabilitySlotSelection.objects.create(grid=grid, name="Opener", **_slot(_when(11)))
    second = AvailabilitySlotSelection.objects.create(grid=grid, name="Team TT", **_slot(_when(18)))

    moved, blocked = series.move_scheduled_races(grid, [(_when(11), _when(18)), (_when(18), _when(11))])

    first.refresh_from_db()
    second.refresh_from_db()
    assert (len(moved), blocked) == (2, [])
    assert _slot(_when(18)) == {"slot_date": first.slot_date, "slot_time": first.slot_time}
    assert _slot(_when(11)) == {"slot_date": second.slot_date, "slot_time": second.slot_time}


def _slot(starts_at: datetime) -> dict:
    """Locate a scheduled race at a start time.

    Returns:
        ``slot_date`` and ``slot_time`` kwargs.

    """
    slot_date, slot_time = series.slot_key(starts_at)
    return {"slot_date": slot_date, "slot_time": slot_time}


@pytest.mark.django_db
def test_a_race_whose_new_time_is_taken_stays_put(squad, stages) -> None:
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    race = AvailabilitySlotSelection.objects.create(grid=grid, name="Opener", **_slot(_when(11)))
    AvailabilitySlotSelection.objects.create(grid=grid, name="Leftover", **_slot(_when(30)))

    moved, blocked = series.move_scheduled_races(grid, [(_when(11), _when(30))])

    race.refresh_from_db()
    assert (moved, [r.pk for r in blocked]) == ([], [race.pk])
    assert {"slot_date": race.slot_date, "slot_time": race.slot_time} == _slot(_when(11))


@pytest.mark.django_db
def test_windows_read_and_fill(stages) -> None:
    opener = stages[0]
    tuesday = series.window_weekday(opener, 1)

    assert tuesday.weekday() == 1
    assert opener.window_start <= tuesday <= opener.window_end
    assert series.window_weekday(stages[2], 1) is None  # no window to look in
    assert series.window_label(stages[2]) == ""
    assert "\u2013" in series.window_label(opener)


@pytest.mark.parametrize(
    ("available", "maybe", "expected"),
    [(2, 0, "enough"), (1, 1, "tight"), (1, 0, "short")],
)
def test_the_maybes_decide_between_tight_and_short(available, maybe, expected) -> None:
    stage = SeriesStage(name="Opener", riders_min=2)
    assert series.riders_status(stage, series.Tally(available=available, maybe=maybe))[0] == expected
    assert series.riders_status(SeriesStage(name="Open"), series.Tally()) is None


# --- The event's availability type -------------------------------------------------------------


def _event_post(event, **extra) -> dict:
    """Build the minimum event edit POST.

    Returns:
        The POST data.

    """
    return {
        "title": event.title,
        "start_date": event.start_date.isoformat(),
        "end_date": event.end_date.isoformat(),
        "visible": "on",
        **extra,
    }


@pytest.mark.django_db
def test_the_event_form_sets_the_availability_type(client, event, event_admin) -> None:
    event.availability_type = Event.AvailabilityType.WEEKLY
    event.save()
    client.force_login(event_admin)

    client.post(reverse("events:event_edit", args=[event.pk]), _event_post(event, availability_type="series"))

    event.refresh_from_db()
    assert event.is_race_series


@pytest.mark.django_db
def test_a_post_without_the_type_keeps_it(client, event, event_admin) -> None:
    client.force_login(event_admin)
    client.post(reverse("events:event_edit", args=[event.pk]), _event_post(event))
    event.refresh_from_db()
    assert event.is_race_series


@pytest.mark.django_db
def test_a_series_with_published_stage_times_cannot_go_back_to_weekly(
    client, event, event_admin, squad, stages
) -> None:
    _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    client.force_login(event_admin)

    response = client.post(
        reverse("events:event_edit", args=[event.pk]), _event_post(event, availability_type="weekly")
    )

    assert response.status_code == 200
    assert "can't go back to weekly" in response.context["form"].errors["availability_type"][0]
    event.refresh_from_db()
    assert event.is_race_series


@pytest.mark.django_db
def test_a_weekly_event_with_published_sheets_can_become_a_series(client, event, event_admin, squad) -> None:
    event.availability_type = Event.AvailabilityType.WEEKLY
    event.save()
    weekly = AvailabilityGrid.objects.create(
        squad=squad,
        status=PUBLISHED,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=6),
        start_time="18:00",
        end_time="20:00",
        slot_duration=60,
    )
    client.force_login(event_admin)

    response = client.post(
        reverse("events:event_edit", args=[event.pk]), _event_post(event, availability_type="series")
    )

    assert response.status_code == 302
    event.refresh_from_db()
    weekly.refresh_from_db()
    assert event.is_race_series
    assert (weekly.kind, weekly.status) == (AvailabilityGrid.Kind.WEEKLY, PUBLISHED)  # untouched, still answerable


@pytest.mark.django_db
def test_a_refused_save_says_so_at_the_top_of_the_form(client, event, event_admin, squad, stages) -> None:
    _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    client.force_login(event_admin)

    body = client.post(
        reverse("events:event_edit", args=[event.pk]), _event_post(event, availability_type="weekly")
    ).content.decode()

    summary = body.index('id="event-form-errors"')
    assert "Not saved" in body[summary:]
    assert summary < body.index('id="id_title"')  # above the first field, where the page opens


@pytest.mark.django_db
def test_creating_a_series_event_goes_to_its_stages(client, event_admin) -> None:
    client.force_login(event_admin)
    today = date.today()
    response = client.post(
        reverse("events:event_create"),
        {
            "title": "Spring Series",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=30)).isoformat(),
            "availability_type": "series",
        },
    )
    created = Event.objects.get(title="Spring Series")
    assert response.url == reverse("events:series_stages", args=[created.pk])


# --- The Stages page ---------------------------------------------------------------------------

PREFIX = "series_stages"


def _formset(rows: list[dict], *, initial: int = 0) -> dict:
    """Build the Stages page's POST.

    Returns:
        The POST data.

    """
    data = {
        f"{PREFIX}-TOTAL_FORMS": str(len(rows)),
        f"{PREFIX}-INITIAL_FORMS": str(initial),
        f"{PREFIX}-MIN_NUM_FORMS": "0",
        f"{PREFIX}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        defaults = {"stage_type": "points", "laps": "1", "order": str(index)}
        for key, value in {**defaults, **row}.items():
            data[f"{PREFIX}-{index}-{key}"] = value
    return data


@pytest.mark.django_db
def test_only_event_admins_reach_the_stages_page(client, event, team_member) -> None:
    client.force_login(team_member)
    response = client.get(reverse("events:series_stages", args=[event.pk]))
    assert response.url == reverse("events:event_detail", args=[event.pk])


@pytest.mark.django_db
def test_stages_are_added_in_the_order_given(client, event, event_admin, route) -> None:
    client.force_login(event_admin)

    response = client.post(
        reverse("events:series_stages", args=[event.pk]),
        _formset([
            {"name": "Second", "order": "5", "route": str(route.pk), "laps": "3"},
            {"name": "First", "order": "1", "stage_type": "ttt"},
            {"name": "", "order": "9"},  # the blank "add" row the page always offers
        ]),
    )

    assert response.status_code == 302
    assert [(s.name, s.order) for s in event.series_stages.all()] == [("First", 0), ("Second", 1)]
    assert event.series_stages.get(name="Second").route == route


@pytest.mark.django_db
def test_moving_stages_alone_is_saved(client, event, event_admin, stages) -> None:
    client.force_login(event_admin)
    rows = [
        {"id": str(stage.pk), "name": stage.name, "stage_type": stage.stage_type, "order": str(order)}
        for stage, order in zip(stages, [1, 0, 2], strict=True)
    ]

    client.post(reverse("events:series_stages", args=[event.pk]), _formset(rows, initial=len(rows)))

    assert [s.name for s in event.series_stages.all()] == ["Team TT", "Opener", "Climb"]


@pytest.mark.django_db
def test_an_answered_stage_cannot_be_removed(client, event, event_admin, user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    _answer(grid, rider, stages[0], "available")
    client.force_login(event_admin)
    rows = [
        {"id": str(stage.pk), "name": stage.name, "stage_type": stage.stage_type, "order": str(index)}
        for index, stage in enumerate(stages)
    ]
    rows[0]["DELETE"] = "on"

    response = client.post(reverse("events:series_stages", args=[event.pk]), _formset(rows, initial=len(rows)))

    assert response.status_code == 200
    assert any("so it can't be removed" in error for error in response.context["formset"].non_form_errors())
    assert SeriesStage.objects.filter(pk=stages[0].pk).exists()


# --- A squad's stage times ---------------------------------------------------------------------


def _times_post(stages, starts: list[tuple[date, str] | None], tz: str = "Europe/London", **extra) -> dict:
    """Build the stage-times POST; None marks a stage not raced.

    Returns:
        The POST data.

    """
    data = {"timezone": tz, **extra}
    for stage, start in zip(stages, starts, strict=True):
        if start is None:
            data[f"reason_{stage.pk}"] = "Bye week"
            continue
        data[f"racing_{stage.pk}"] = "on"
        data[f"date_{stage.pk}"] = start[0].isoformat()
        data[f"time_{stage.pk}"] = start[1]
    return data


@pytest.mark.django_db
def test_a_captain_saves_stage_times_as_a_draft(client, event, event_admin, squad, stages) -> None:
    client.force_login(event_admin)
    day = date(2027, 1, 12)  # winter: London is on UTC

    response = client.post(
        reverse("events:series_sheet", args=[event.pk, squad.pk]),
        _times_post(stages, [(day, "19:30"), (day + timedelta(days=7), "19:30"), None]),
    )

    assert response.url == reverse("events:squad_availability", args=[event.pk, squad.pk])
    grid = series.series_sheet(squad)
    assert (grid.kind, grid.status, grid.grid_timezone) == (SERIES, "draft", "Europe/London")
    by_stage = {ss.stage_id: ss for ss in grid.sheet_stages.all()}
    assert by_stage[stages[0].pk].starts_at == datetime(2027, 1, 12, 19, 30, tzinfo=UTC)
    assert not by_stage[stages[2].pk].racing
    assert by_stage[stages[2].pk].not_racing_reason == "Bye week"
    assert (grid.start_date, grid.end_date) == (day, day + timedelta(days=7))


@pytest.mark.django_db
def test_stage_times_must_hold_together(client, event, event_admin, squad, stages) -> None:
    client.force_login(event_admin)
    day = date(2027, 1, 12)
    url = reverse("events:series_sheet", args=[event.pk, squad.pk])

    missing = client.post(url, {**_times_post(stages, [(day, "19:30"), None, None]), f"racing_{stages[1].pk}": "on"})
    clash = client.post(url, _times_post(stages, [(day, "19:30"), (day, "19:30"), None]))
    none = client.post(url, _times_post(stages, [None, None, None]))

    assert "or switch it to not racing" in missing.content.decode()
    assert "already starts at this time" in clash.content.decode()
    assert "Race at least one stage." in none.content.decode()
    assert series.series_sheet(squad) is None


@pytest.mark.django_db
def test_moving_a_stage_asks_only_its_riders_again_and_moves_its_race(
    client, event, event_admin, user_model, squad, stages
) -> None:
    ana = _member(user_model, squad, "ana", discord_id="1001")
    bo = _member(user_model, squad, "bo", discord_id="1002")
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    _answer(grid, ana, stages[0], "available")
    _answer(grid, bo, stages[1], "maybe")
    race = AvailabilitySlotSelection.objects.create(grid=grid, name="Opener", **_slot(_when(11)))
    client.force_login(event_admin)
    moved_to = _when(12)

    with patch("apps.events.series_views.send_discord_channel_message", return_value=True) as send:
        client.post(
            reverse("events:series_sheet", args=[event.pk, squad.pk]),
            _times_post(
                stages,
                [(moved_to.date(), "19:30"), (_when(18).date(), "19:30"), (_when(25).date(), "19:30")],
                tz="UTC",
                notify_moved="on",
            ),
        )

    sheet_stages = {ss.stage_id: ss for ss in grid.sheet_stages.all()}
    ana_answers = AvailabilityResponse.objects.get(grid=grid, user=ana).stage_answers
    bo_answers = AvailabilityResponse.objects.get(grid=grid, user=bo).stage_answers
    assert series.answer_state(ana_answers, sheet_stages[stages[0].pk]) == series.STALE
    assert series.answer_state(bo_answers, sheet_stages[stages[1].pk]) == "maybe"
    race.refresh_from_db()
    assert {"slot_date": race.slot_date, "slot_time": race.slot_time} == _slot(moved_to)
    send.assert_called_once()
    assert send.call_args.kwargs["allowed_user_ids"] == ["1001"]
    assert "<@1001>" in send.call_args.args[1]
    assert "<@1002>" not in send.call_args.args[1]


@pytest.mark.django_db
def test_a_weekly_event_has_no_stage_times(client, event, event_admin, squad, stages) -> None:
    event.availability_type = Event.AvailabilityType.WEEKLY
    event.save()
    client.force_login(event_admin)
    response = client.get(reverse("events:series_sheet", args=[event.pk, squad.pk]))
    assert response.url == reverse("events:squad_availability", args=[event.pk, squad.pk])


@pytest.mark.django_db
def test_the_grid_builder_sends_a_series_squad_to_its_stage_times(client, event, event_admin, squad) -> None:
    client.force_login(event_admin)
    response = client.get(reverse("events:availability_create", args=[event.pk, squad.pk]))
    assert response.url == reverse("events:series_sheet", args=[event.pk, squad.pk])


@pytest.mark.django_db
def test_times_can_be_copied_from_another_squad(client, event, event_admin, squad, stages) -> None:
    other = Squad.objects.create(event=event, name="A2")
    _sheet(other, stages, [datetime(2027, 1, 12, 19, 0, tzinfo=UTC), None, None])
    client.force_login(event_admin)

    response = client.get(reverse("events:series_sheet", args=[event.pk, squad.pk]), {"copy_from": other.pk})

    form = response.context["form"]
    assert form.initial[f"date_{stages[0].pk}"] == date(2027, 1, 12)
    assert form.initial[f"racing_{stages[1].pk}"] is False
    assert series.series_sheet(squad) is None  # copying saves nothing


@pytest.mark.django_db
def test_a_series_sheet_cannot_be_copied(client, event, event_admin, squad, stages) -> None:
    grid = _sheet(squad, stages, [_when(11), _when(18), _when(25)])
    client.force_login(event_admin)
    client.post(
        reverse("events:availability_copy", args=[event.pk, squad.pk, grid.pk]),
        {"start_date": "2027-01-01", "end_date": "2027-01-07"},
    )
    assert AvailabilityGrid.objects.filter(squad=squad).count() == 1


# --- The rider's answers -----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_rider_answers_each_stage(client, event, user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    client.force_login(rider)
    url = reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk])

    page = client.get(url).content.decode()
    response = client.post(
        url,
        {f"answer_{stages[0].pk}": "available", f"note_{stages[0].pk}": "Might join late"},
    )

    assert 'aria-label="Available"' in page
    assert "A1 isn't racing this stage." in page
    assert response.url == url
    answers = AvailabilityResponse.objects.get(grid=grid, user=rider).stage_answers
    assert answers == {
        str(stages[0].pk): {"answer": "available", "for": series.stamp(_when(11)), "note": "Might join late"}
    }


@pytest.mark.django_db
def test_a_bad_answer_saves_nothing(client, event, user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    client.force_login(rider)
    url = reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk])

    too_long = client.post(url, {f"answer_{stages[0].pk}": "maybe", f"note_{stages[0].pk}": "x" * 201})
    unknown = client.post(url, {f"answer_{stages[0].pk}": "yes"})

    assert (too_long.status_code, unknown.status_code) == (200, 200)
    assert not AvailabilityResponse.objects.filter(grid=grid, user=rider).exists()


@pytest.mark.django_db
def test_a_stage_that_has_started_is_shown_but_not_asked(client, event, user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(-2), _when(18), None])
    client.force_login(rider)
    url = reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk])

    page = client.get(url).content.decode()
    client.post(url, {f"answer_{stages[0].pk}": "no", f"answer_{stages[1].pk}": "available"})

    assert "Already raced" in page
    assert f'name="answer_{stages[0].pk}"' not in page
    answers = AvailabilityResponse.objects.get(grid=grid, user=rider).stage_answers
    assert set(answers) == {str(stages[1].pk)}


@pytest.mark.django_db
def test_race_verified_events_refuse_unverified_answers(client, event, user_model, squad, stages) -> None:
    event.require_race_verified_availability = True
    event.save()
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    client.force_login(rider)

    client.post(
        reverse("events:availability_respond", args=[event.pk, squad.pk, grid.pk]),
        {f"answer_{stages[0].pk}": "available"},
    )

    assert not AvailabilityResponse.objects.filter(grid=grid, user=rider).exists()


@pytest.mark.django_db
def test_the_badge_asks_until_every_stage_has_a_current_answer(rf, user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    request = rf.get("/")
    request.user = rider

    def badge() -> int:
        cache.clear()
        return pending_availability_count(request)["pending_availability_count"]

    assert badge() == 1
    _answer(grid, rider, stages[0], "available")
    assert badge() == 1  # one stage still unanswered
    _answer(grid, rider, stages[1], "no")
    assert badge() == 0
    grid.sheet_stages.filter(stage=stages[1]).update(starts_at=_when(19))
    assert badge() == 1  # moved: asked again


# --- The captain's results ---------------------------------------------------------------------


@pytest.mark.django_db
def test_results_show_each_riders_answer_per_stage(client, event, user_model, squad, stages) -> None:
    ana = _member(user_model, squad, "ana")
    bo = _member(user_model, squad, "bo")
    _member(user_model, squad, "cy")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    _answer(grid, ana, stages[0], "available", note="Might join late")
    _answer(grid, bo, stages[0], "maybe")
    _answer(grid, bo, stages[1], "available", at=_when(17))  # given before the stage moved
    client.force_login(ana)

    response = client.get(reverse("events:availability_results", args=[event.pk, squad.pk, grid.pk]))

    columns = response.context["columns"]
    assert (columns[0]["counts"].available, columns[0]["counts"].maybe) == (1, 1)
    assert columns[0]["status"] == "Tight"
    assert columns[1]["counts"].stale == 1
    assert not columns[2]["is_open"]
    cells = {row["user"].username: [cell["label"] for cell in row["cells"]] for row in response.context["rows"]}
    assert cells["bo"] == ["Maybe", "Old time", "—"]
    assert cells["cy"] == ["No answer", "No answer", "—"]
    assert response.context["notes"] == []  # riders' notes are for captains
    assert "openSlotModal" not in response.content.decode()


@pytest.mark.django_db
def test_captains_get_notes_and_a_lineup_dialog_prefilled_from_the_stage(
    client, event, event_admin, user_model, squad, stages
) -> None:
    ana = _member(user_model, squad, "ana")
    bo = _member(user_model, squad, "bo")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    _answer(grid, ana, stages[0], "available", note="Might join late")
    _answer(grid, bo, stages[0], "maybe")
    client.force_login(event_admin)

    response = client.get(reverse("events:availability_results", args=[event.pk, squad.pk, grid.pk]))

    key = "{}|{}".format(*series.slot_key(_when(11)))
    assert [note["note"] for note in response.context["notes"]] == ["Might join late"]
    assert response.context["utc_cell_users_json"][key] == [ana.pk]
    assert response.context["utc_cell_notes_json"][key] == {str(bo.pk): "(maybe)"}
    defaults = response.context["slot_defaults_json"][key]
    assert (defaults["name"], defaults["laps"]) == ("Stage 1: Opener", 3)
    assert defaults["course_url"].endswith(reverse("routes:detail", args=["volcano"]))
    assert "openSlotModal" in response.content.decode()


# --- The series board --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_board_is_for_the_people_running_the_event(client, event, team_member, event_admin) -> None:
    client.force_login(team_member)
    assert client.get(reverse("events:series_board", args=[event.pk])).status_code == 302
    client.force_login(event_admin)
    assert client.get(reverse("events:series_board", args=[event.pk])).status_code == 200


@pytest.mark.django_db
def test_the_board_flags_what_needs_attention(client, event, event_admin, user_model, squad, stages) -> None:
    ana = _member(user_model, squad, "ana")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    _answer(grid, ana, stages[1], "available")
    race = AvailabilitySlotSelection.objects.create(
        grid=grid, name="Opener", status=AvailabilitySlotSelection.Status.CONFIRMED, **_slot(_when(11))
    )
    race.selected_users.add(ana)
    draft_squad = Squad.objects.create(event=event, name="B1")
    _sheet(draft_squad, stages, [_when(12), None, None], status=AvailabilityGrid.Status.DRAFT)
    Squad.objects.create(event=event, name="C1")
    client.force_login(event_admin)

    response = client.get(reverse("events:series_board", args=[event.pk]))

    rows = {row["squad"].name: row["cells"] for row in response.context["rows"]}
    assert rows["A1"][0]["chip"] == "Lineup set · 1"
    assert rows["A1"][1]["chip"] == "Short"
    assert rows["A1"][2]["chip"] == "Not racing"
    assert rows["B1"][0]["chip"] == "Draft"
    assert rows["C1"][0]["chip"] == "No sheet"
    tags = sorted(item["tag"] for item in response.context["attention"])
    assert tags == ["Draft", "No sheet", "Short"]


# --- My Events, and the participation report ---------------------------------------------------


@pytest.mark.django_db
def test_my_events_lists_my_stages(client, event, user_model, squad, stages) -> None:
    rider = _member(user_model, squad, "ana")
    from apps.events.models import EventSignup

    EventSignup.objects.create(event=event, user=rider, status=EventSignup.Status.REGISTERED)
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    _answer(grid, rider, stages[0], "available")
    _answer(grid, rider, stages[1], "maybe")
    race = AvailabilitySlotSelection.objects.create(
        grid=grid, name="Opener", status=AvailabilitySlotSelection.Status.CONFIRMED, **_slot(_when(11))
    )
    race.selected_users.add(rider)
    client.force_login(rider)

    response = client.get(reverse("events:my_events"))

    squad_data = response.context["events_data"][0]["squads"][0]
    assert [row["label"] for row in squad_data["series"]["stages"]] == [
        "You're racing",
        "You said Maybe",
        "Not racing",
    ]
    assert not squad_data["series"]["needs_answer"]
    assert squad_data["grids"][0].user_responded
    assert "Add to calendar" in response.content.decode()


@pytest.mark.django_db
def test_the_participation_report_counts_available_stages(
    client, event, event_admin, user_model, squad, stages
) -> None:
    ana = _member(user_model, squad, "ana")
    bo = _member(user_model, squad, "bo")
    grid = _sheet(squad, stages, [_when(11), _when(18), None])
    _answer(grid, ana, stages[0], "available")
    _answer(grid, ana, stages[1], "available")
    _answer(grid, bo, stages[0], "maybe")
    client.force_login(event_admin)

    response = client.get(reverse("events:event_all_races", args=[event.pk]) + "?tab=participation")

    group = next(g for g in response.context["participation"] if g["squad"].pk == squad.pk)
    states = {row["user"].username: row["availability"][0] for row in group["rows"]}
    assert (states["ana"]["state"], states["ana"]["detail"]) == ("yes", "Available for 2 stages")
    assert states["bo"]["state"] == "no"
